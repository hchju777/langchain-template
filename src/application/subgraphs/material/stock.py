"""자재 소진 예상 — docs/tutorial.md의 실습 결과물.

Redis에서 라인별 자재 재고를 읽어 "몇 시간 뒤에 떨어지는가"를 계산한다.
튜토리얼용이지만 실제로 도는 코드이고, 새 분석을 만들 때 복붙 출발점으로
써도 된다.

등록명은 파일 경로에서 유도된다: material/stock.py → "material.stock"
"""

from __future__ import annotations

from src.application.subgraphs.base import BaseSubgraph, SubgraphConfig, SubgraphState
from src.config.registry import register
from src.domain.models import FetchSpec, Judgement, Metric, Severity, SnapshotContext


class MaterialStockConfig(SubgraphConfig):
    """이 서브그래프가 config에서 받는 값.

    부팅 시 이 스키마로 검증되므로, 오타나 타입 오류가 8시 배치가 아니라
    프로세스 시작 시점에 잡힌다.
    """

    warn_hours: float = 8.0
    critical_hours: float = 2.0
    #: 규칙으로 표현하기 어려운 조합 판단을 LLM에 맡길지. 기본은 끔.
    #: config에 "use_llm_judge": true 한 줄이면 켜진다.
    use_llm_judge: bool = False


@register()
class MaterialStock(BaseSubgraph):
    title = "자재 소진 예상"
    config_model = MaterialStockConfig
    context_type = SnapshotContext  # 현재 재고를 보므로 스냅샷

    async def process(self, state: SubgraphState) -> dict:
        cfg: MaterialStockConfig = self.config

        # 1) 데이터를 가져온다. 어떤 저장소인지는 몰라도 된다 —
        #    deps.redis가 SnapshotPort를 구현했다는 것만 안다.
        records = await self.deps.redis.fetch(
            state.scoped, FetchSpec(kind="material_stock")
        )

        # 2) 로직을 돌린다. 규칙으로 쓸 수 있으면 코드가 이긴다.
        metrics, judgements = [], []
        for rec in records:
            line = rec.metadata["line"]
            material = rec.metadata["material"]
            stock = rec.record["stock_qty"]
            rate = rec.record["hourly_consumption"]
            hours_left = stock / rate if rate else float("inf")

            # 3) 숫자는 metrics에 담는다. 템플릿이 그대로 렌더링하고
            #    LLM은 이 값을 다시 쓰지 않는다.
            metrics.append(
                Metric(
                    name=f"{line} {material}",
                    value=round(hours_left, 1),
                    unit="시간",
                    note=f"재고 {stock:,}ea · 시간당 {rate}ea 소비",
                )
            )

            # 4) 판정에는 반드시 근거(실제 Record의 id)를 단다.
            if hours_left <= cfg.critical_hours:
                severity = Severity.CRITICAL
            elif hours_left <= cfg.warn_hours:
                severity = Severity.WARNING
            else:
                continue

            judgements.append(
                Judgement(
                    subject=f"{line} {material} 자재 부족",
                    severity=severity,
                    reasoning=(
                        f"재고 {stock:,}ea를 시간당 {rate}ea로 소비하면 "
                        f"{hours_left:.1f}시간 뒤 소진됩니다"
                    ),
                    evidence=[rec.id],
                )
            )

        # 5) 규칙으로 표현하기 어려운 판단만 LLM에 넘긴다.
        #    위 임계치 비교는 코드가 이긴다 — 빠르고 싸고 항상 같은 답이 나온다.
        #    반면 "A와 B가 같은 라인에서 동시에 떨어지는 조합이 위험한가" 같은
        #    건 임계치로 표현할 수 없다. 그런 것만 여기서 맡긴다.
        if cfg.use_llm_judge and records:
            judgements.extend(await self._judge_combinations(records))

        # 6) 이 셋을 돌려주면 나머지는 BaseSubgraph가 처리한다.
        #    generate_output이 LLM 서술을 붙여 ReportSection을 만들고,
        #    취합 노드가 모아 리포트를 찍는다.
        return {"records": records, "metrics": metrics, "judgements": judgements}

    async def _judge_combinations(self, records: list) -> list[Judgement]:
        """LLM에게 조합 판단을 맡긴다.

        지켜야 할 것이 셋 있다.

        1. 프롬프트에 **id를 함께 실어준다** — [stock:L1:MAT-A] 형태.
           LLM이 근거를 인용할 때 쓸 수 있는 어휘를 주는 것이다.
        2. 사실은 '- '로 시작하는 줄로 준다 — 렌더러·가짜 LLM이 지시문과
           사실을 구분하는 약속이다.
        3. judge()의 세 번째 인자로 **허용된 id 목록**을 넘긴다. LLM이 여기
           없는 id를 근거로 대면 그 판정은 폐기되고 경고가 뜬다(환각 가드레일).
           이 검증은 LLM 없이 코드로 돌기 때문에 항상 신뢰할 수 있다.

        반환된 Judgement는 코드가 만든 것과 똑같은 스키마라서, 리포트에서
        구분 없이 섞인다. 근거 추적도 동일하게 된다.
        """
        facts = "\n".join(
            f"- [{r.id}] {r.metadata['line']} {r.metadata['material']}: "
            f"재고 {r.record['stock_qty']:,}ea, "
            f"시간당 {r.record['hourly_consumption']}ea 소비"
            for r in records
        )
        prompt = (
            "아래는 라인별 자재 재고 현황입니다. 개별 임계치로는 잡히지 않는 "
            "위험 조합(같은 라인의 자재가 동시에 소진되는 경우 등)이 있는지 "
            "판단하세요. 근거는 반드시 대괄호 안의 id로만 인용하세요.\n"
            f"{facts}"
        )
        return await self.deps.llm.judge(
            self.registry_name, prompt, [r.id for r in records]
        )
