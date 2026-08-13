"""KPI 점검 — 여러 KPI가 목표 대비 어떤지 확인.

목표 대비 이탈은 규칙으로 판정한다(코드). 그 위에 LLM judge를 얹어
"규칙으로는 각각 정상이지만 조합이 이상한" 경우를 보게 한다.

LLM이 든 근거는 실제 입력 record id와 대조되고, 없는 id를 대면 그 판정은
폐기된다. 이 가드레일은 LLM 없이 도는 결정론적 검증이라 항상 켜 둔다.
"""

from __future__ import annotations

from src.application.subgraphs.base import BaseSubgraph, SubgraphConfig, SubgraphState
from src.config.registry import register
from src.domain.models import FetchSpec, Judgement, Metric, Severity, SnapshotContext


class KpiCheckConfig(SubgraphConfig):
    warn_gap_pct: float = -3.0
    critical_gap_pct: float = -8.0
    use_llm_judge: bool = True


@register()
class KpiCheck(BaseSubgraph):
    title = "KPI 점검"
    config_model = KpiCheckConfig
    context_type = SnapshotContext

    async def process(self, state: SubgraphState) -> dict:
        cfg: KpiCheckConfig = self.config
        records = await self.deps.rest.fetch(state.scoped, FetchSpec(kind="kpi"))

        metrics, judgements = [], []
        for rec in records:
            name = rec.metadata["kpi"]
            unit = rec.metadata.get("unit", "")
            value = rec.record["value"]
            target = rec.record["target"]
            gap = rec.record["gap_pct"]

            metrics.append(
                Metric(
                    name=name,
                    value=value,
                    unit=unit,
                    note=f"목표 {target}{unit} · 괴리 {gap:+.1f}%",
                )
            )

            if gap <= cfg.critical_gap_pct:
                severity = Severity.CRITICAL
            elif gap <= cfg.warn_gap_pct:
                severity = Severity.WARNING
            else:
                continue

            judgements.append(
                Judgement(
                    subject=f"{name} 목표 미달",
                    severity=severity,
                    reasoning=(
                        f"{value}{unit}로 목표 {target}{unit} 대비 {gap:+.1f}%입니다"
                    ),
                    evidence=[rec.id],
                )
            )

        # 규칙으로 표현하기 어려운 조합 판단은 LLM에 맡긴다.
        if cfg.use_llm_judge and records:
            facts = "\n".join(
                f"- [{r.id}] {r.metadata['kpi']}: {r.record['value']}"
                f"{r.metadata.get('unit', '')} (목표 대비 {r.record['gap_pct']:+.1f}%)"
                for r in records
            )
            prompt = (
                "아래 KPI 조합에서 개별 임계치로는 잡히지 않는 이상 신호가 있는지 "
                "판단하세요. 근거는 반드시 대괄호 안의 id로만 인용하세요.\n" + facts
            )
            judgements.extend(
                await self.deps.llm.judge(
                    self.registry_name, prompt, [r.id for r in records]
                )
            )

        return {"records": records, "metrics": metrics, "judgements": judgements}
