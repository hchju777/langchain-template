"""KPI 점검 — 여러 KPI가 목표 대비 어떤지 확인.

목표 대비 이탈은 규칙으로 판정한다(코드). 그 위에 LLM judge를 얹어
"규칙으로는 각각 정상이지만 조합이 이상한" 경우를 보게 한다.

LLM이 든 근거는 실제 입력 record id와 대조되고, 없는 id를 대면 그 판정은
폐기된다. 이 가드레일은 LLM 없이 도는 결정론적 검증이라 항상 켜 둔다.

KPI가 미달이면 설비 상태나 생산 실적을 더 봐야 원인을 알 수 있을 때가 있다.
알람(mongodb)은 구간 조회가 필요해 스냅샷 분석인 이 서브그래프의 probe로는
쓸 수 없다 — ProcessGraph가 조립 시점에 컨텍스트 타입을 대조해 이런 조합을
막는다.
그래서 process를 fetch/compute/judge로 나누고 ProcessGraph로 감싸,
judge 뒤에 추가 조회 여부를 판단하게 한다.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.application.subgraphs.base import BaseSubgraph, SubgraphConfig, SubgraphState
from src.application.subgraphs.process_graph import Probe, ProcessGraph
from src.config.registry import register
from src.domain.models import FetchSpec, Judgement, Metric, Record, Severity, SnapshotContext


class KpiCheckConfig(SubgraphConfig):
    warn_gap_pct: float = -3.0
    critical_gap_pct: float = -8.0
    use_llm_judge: bool = True


class _KindProbe(Probe):
    """kind 하나를 그대로 가져오는 단일 노드 probe."""

    kind: str = ""
    #: 안전한 기본값이 없어 Probe가 선언을 강제한다. 여기서 여는 kind는
    #: 전부 스냅샷이라 SnapshotContext로 충분하다.
    context_type = SnapshotContext

    def compile(self, deps, config):
        async def step(state: SubgraphState) -> dict:
            records = await deps.data.fetch(state.scoped, FetchSpec(kind=self.kind))
            return {"probe_records": [*state.probe_records, *records]}

        g = StateGraph(SubgraphState)
        g.add_node("step", step)
        g.add_edge(START, "step")
        g.add_edge("step", END)
        return g.compile()


class EquipmentProbe(_KindProbe):
    name = "equipment"
    kind = "equipment_status"
    kinds = ("equipment_status",)
    description = "미달 라인의 설비 가동 상태를 확인한다"


class ProductionProbe(_KindProbe):
    name = "production"
    kind = "production"
    kinds = ("production",)
    description = "미달 라인의 생산 실적을 확인한다"


@register()
class KpiCheck(BaseSubgraph):
    title = "KPI 점검"
    config_model = KpiCheckConfig
    context_type = SnapshotContext
    # probe가 여는 것까지 선언한다. "이 분석이 건드릴 수 있는 전부"를 적는
    # 기존 규약이 그대로 이어지고, 부팅 검증이 별도 규칙 없이 적용된다.
    required_kinds = ("kpi", "equipment_status", "production")

    def build_process(self):
        return ProcessGraph(self, (EquipmentProbe(), ProductionProbe())).compile()

    async def fetch(self, state: SubgraphState) -> dict:
        return {"records": await self.deps.data.fetch(
            state.scoped, FetchSpec(kind="kpi"))}

    async def compute(self, state: SubgraphState) -> dict:
        """지표만 만든다. base records만 보므로 한 번만 돌면 된다."""
        metrics = []
        for rec in state.records:
            unit = rec.metadata.get("unit", "")
            metrics.append(
                Metric(
                    name=rec.metadata["kpi"],
                    value=rec.record["value"],
                    unit=unit,
                    note=(f"목표 {rec.record['target']}{unit} · "
                          f"괴리 {rec.record['gap_pct']:+.1f}%"),
                )
            )
        return {"metrics": metrics}

    async def judge(self, state: SubgraphState) -> dict:
        """판정을 통째로 새로 만든다.

        임계치 판정은 base records만 보므로 매 라운드 다시 계산해도 결과가
        같다. 그래서 교체해도 잃는 것이 없고, 한 섹션에 모순된 판정이
        남지도 않는다.
        """
        cfg: KpiCheckConfig = self.config
        judgements = []
        for rec in state.records:
            unit = rec.metadata.get("unit", "")
            gap = rec.record["gap_pct"]
            if gap <= cfg.critical_gap_pct:
                severity = Severity.CRITICAL
            elif gap <= cfg.warn_gap_pct:
                severity = Severity.WARNING
            else:
                continue
            judgements.append(
                Judgement(
                    subject=f"{rec.metadata['kpi']} 목표 미달",
                    severity=severity,
                    reasoning=(f"{rec.record['value']}{unit}로 목표 "
                               f"{rec.record['target']}{unit} 대비 {gap:+.1f}%입니다"),
                    evidence=[rec.id],
                )
            )

        # probe로 늘어난 것까지 근거 후보에 넣는다. 빠뜨리면 probe 데이터를
        # 인용한 판정이 통째로 폐기된다.
        seen = state.records + state.probe_records
        if cfg.use_llm_judge and seen:
            judgements.extend(await self.deps.llm.judge(
                self.registry_name, self._judge_prompt(seen), [r.id for r in seen]))
        return {"judgements": judgements}

    def _judge_prompt(self, records: list) -> str:
        facts = "\n".join(f"- [{r.id}] {r.metadata} {r.record}" for r in records)
        return (
            "아래 자료에서 개별 임계치로는 잡히지 않는 이상 신호가 있는지 "
            "판단하세요. 근거는 반드시 대괄호 안의 id로만 인용하세요.\n" + facts
        )
