"""알람 추세 — 최근 7일간 scen_id별 발생 추이.

유일한 구간(historical) 분석이다. config에 상대 시간("7d")으로 적고
validate_input이 as_of 기준 절대 시각 쌍으로 바꾼다. 그래서 어제 리포트를
다시 뽑아도 같은 구간을 본다.

구간 조회는 Kafka가 아니라 MongoDB에서 한다. Kafka는 retention 밖을
조회할 수 없고 인덱스도 필터도 없어 구간 전체를 전량 소비해야 한다.
"""

from __future__ import annotations

from collections import defaultdict

from src.application.subgraphs.base import BaseSubgraph, SubgraphConfig, SubgraphState
from src.config.registry import register
from src.domain.models import (
    FetchSpec,
    HistoricalContext,
    Judgement,
    Metric,
    Severity,
)


class AlarmTrendConfig(SubgraphConfig):
    spike_ratio: float = 1.5   # 후반 절반이 전반 절반의 몇 배면 증가로 볼 것인가
    min_total: int = 5         # 표본이 적으면 추세로 보지 않는다
    top_n: int = 5


@register()
class AlarmTrend(BaseSubgraph):
    title = "알람 추세 (scen_id별)"
    config_model = AlarmTrendConfig
    context_type = HistoricalContext
    required_kinds = ("alarms",)

    async def process(self, state: SubgraphState) -> dict:
        cfg: AlarmTrendConfig = self.config
        ctx: HistoricalContext = state.scoped  # type: ignore[assignment]
        records = await self.deps.data.fetch(ctx, FetchSpec(kind="alarms"))

        days = max(1, (ctx.end_dt - ctx.start_dt).days)
        half = days / 2

        by_scen: dict[str, list] = defaultdict(list)
        for rec in records:
            by_scen[rec.metadata["scen_id"]].append(rec)

        metrics, judgements = [], []
        ranked = sorted(by_scen.items(), key=lambda kv: -len(kv[1]))

        for scen_id, recs in ranked[: cfg.top_n]:
            title = recs[0].metadata["title"]
            early = [r for r in recs if r.metadata["day_offset"] < half]
            late = [r for r in recs if r.metadata["day_offset"] >= half]
            trend_ratio = (len(late) / len(early)) if early else float("inf")

            if not early:
                arrow = "신규"
            elif trend_ratio >= cfg.spike_ratio:
                arrow = f"증가 ×{trend_ratio:.1f}"
            elif trend_ratio <= 1 / cfg.spike_ratio:
                arrow = f"감소 ×{trend_ratio:.1f}"
            else:
                arrow = "보합"

            critical = sum(1 for r in recs if r.record["severity"] == "CRITICAL")
            metrics.append(
                Metric(
                    name=f"{scen_id} {title}",
                    value=len(recs),
                    unit="건",
                    note=f"{days}일 · 전반 {len(early)} → 후반 {len(late)} · {arrow}"
                         + (f" · CRITICAL {critical}건" if critical else ""),
                )
            )

            if len(recs) >= cfg.min_total and trend_ratio >= cfg.spike_ratio:
                judgements.append(
                    Judgement(
                        subject=f"{scen_id} 알람 증가",
                        severity=(
                            Severity.CRITICAL if critical else Severity.WARNING
                        ),
                        reasoning=(
                            f"'{title}' 알람이 전반 {len(early)}건에서 후반 "
                            f"{len(late)}건으로 {trend_ratio:.1f}배 늘었습니다"
                        ),
                        evidence=[r.id for r in late[:10]],
                    )
                )

        metrics.insert(
            0,
            Metric(
                name="집계 구간",
                value=f"{ctx.start_dt:%m-%d} ~ {ctx.end_dt:%m-%d}",
                note=f"총 {len(records):,}건 · 시나리오 {len(by_scen)}종",
            ),
        )
        return {"records": records, "metrics": metrics, "judgements": judgements}
