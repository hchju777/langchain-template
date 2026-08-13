"""구미 공장 전용 자재 소진 로직 — "상속은 코드에서, 선택은 config에서"의 실례.

구미는 자재 입고가 하루 두 번(오전·오후)이라 다음 입고까지 버티는지가
기준이다. 그래서 단순 소진 시간이 아니라 **다음 입고 시각까지 남는지**를
본다. 임계치만으로는 표현할 수 없어 로직 자체가 달라지는 경우다.

이 클래스는 config에 직접 등록되지 않는다. 대신 슬롯 override 대상으로만
참조된다:

    // config/factories/gumi/mx.json
    { "subgraphs": { "material.stock": {
        "nodes": { "process": "material.stock_gumi" } } } }

그래서 다른 공장은 이 파일의 존재조차 모르고 기본 로직을 쓴다.
"""

from __future__ import annotations

from src.application.subgraphs.base import SubgraphState
from src.application.subgraphs.material.stock import MaterialStock, MaterialStockConfig
from src.config.registry import register
from src.domain.models import FetchSpec, Judgement, Metric, Severity

#: 구미 자재 입고 시각 (시)
REPLENISH_HOURS = (9, 15)


@register(slot_only=True)
class GumiMaterialStock(MaterialStock):
    title = "자재 소진 예상 (구미)"
    config_model = MaterialStockConfig
    required_kinds = ("material_stock",)

    @staticmethod
    def _hours_to_next_replenish(hour: int) -> float:
        """다음 입고까지 남은 시간."""
        for h in REPLENISH_HOURS:
            if hour < h:
                return float(h - hour)
        return float(24 - hour + REPLENISH_HOURS[0])

    async def process(self, state: SubgraphState) -> dict:
        cfg: MaterialStockConfig = self.config
        records = await self.deps.data.fetch(
            state.scoped, FetchSpec(kind="material_stock")
        )
        until_replenish = self._hours_to_next_replenish(state.scoped.as_of.hour)

        metrics, judgements = [], []
        for rec in records:
            line = rec.metadata["line"]
            material = rec.metadata["material"]
            stock = rec.record["stock_qty"]
            rate = rec.record["hourly_consumption"]
            hours_left = stock / rate if rate else float("inf")
            margin = hours_left - until_replenish

            metrics.append(
                Metric(
                    name=f"{line} {material}",
                    value=round(hours_left, 1),
                    unit="시간",
                    note=(
                        f"재고 {stock:,}ea · 다음 입고까지 {until_replenish:.0f}시간 "
                        f"· 여유 {margin:+.1f}시간"
                    ),
                )
            )

            # 기본 로직과 다른 지점: 절대 시간이 아니라 입고까지의 여유로 판정한다
            if margin <= 0:
                severity = Severity.CRITICAL
            elif margin <= cfg.critical_hours:
                severity = Severity.WARNING
            else:
                continue

            judgements.append(
                Judgement(
                    subject=f"{line} {material} 입고 전 소진 우려",
                    severity=severity,
                    reasoning=(
                        f"{hours_left:.1f}시간 뒤 소진되는데 다음 입고는 "
                        f"{until_replenish:.0f}시간 뒤입니다 (여유 {margin:+.1f}시간)"
                    ),
                    evidence=[rec.id],
                )
            )

        if cfg.use_llm_judge and records:
            judgements.extend(await self._judge_combinations(records))

        return {"records": records, "metrics": metrics, "judgements": judgements}
