"""Kafka Lag 점검 — 컨슈머 그룹이 밀리고 있는가.

메시지를 소비하지 않는다. lag = end_offset - committed_offset 이므로
오프셋 메타데이터만 읽으면 되고, 그래서 배치에 가볍게 붙는다.
"""

from __future__ import annotations

from collections import defaultdict

from src.application.subgraphs.base import BaseSubgraph, SubgraphConfig, SubgraphState
from src.config.registry import register
from src.domain.models import FetchSpec, Judgement, Metric, Severity, SnapshotContext


class KafkaLagConfig(SubgraphConfig):
    groups: list[str] = []
    warn_lag: int = 5_000
    critical_lag: int = 30_000
    skew_ratio: float = 3.0  # 파티션 편중 판단 기준


@register()
class KafkaLag(BaseSubgraph):
    title = "Kafka Lag 상태"
    config_model = KafkaLagConfig
    context_type = SnapshotContext
    required_kinds = ("consumer_lag",)

    async def process(self, state: SubgraphState) -> dict:
        cfg: KafkaLagConfig = self.config
        records = await self.deps.data.fetch(
            state.scoped,
            FetchSpec(kind="consumer_lag", filters={"groups": cfg.groups}),
        )

        by_group: dict[str, list] = defaultdict(list)
        for rec in records:
            by_group[rec.metadata["group"]].append(rec)

        metrics, judgements = [], []
        for group, recs in sorted(by_group.items()):
            lags = [r.record["lag"] for r in recs]
            total, worst = sum(lags), max(lags)
            worst_rec = max(recs, key=lambda r: r.record["lag"])

            metrics.append(
                Metric(
                    name=f"{group} 총 lag",
                    value=total,
                    unit="건",
                    note=f"{len(recs)}개 파티션 · 최대 {worst:,}",
                )
            )

            if total >= cfg.critical_lag:
                severity = Severity.CRITICAL
            elif total >= cfg.warn_lag:
                severity = Severity.WARNING
            else:
                severity = Severity.NORMAL

            if severity is not Severity.NORMAL:
                judgements.append(
                    Judgement(
                        subject=f"{group} 컨슈머 지연",
                        severity=severity,
                        reasoning=(
                            f"총 lag {total:,}건이 임계치 "
                            f"{cfg.critical_lag if severity is Severity.CRITICAL else cfg.warn_lag:,}"
                            f"건을 넘었습니다"
                        ),
                        evidence=[r.id for r in recs],
                    )
                )

            # 파티션 편중: 총량이 괜찮아도 한 파티션만 밀리면 소비자 하나가 죽은 것
            avg = total / len(recs) if recs else 0
            if avg > 0 and worst > avg * cfg.skew_ratio:
                judgements.append(
                    Judgement(
                        subject=f"{group} 파티션 편중",
                        severity=Severity.WARNING,
                        reasoning=(
                            f"파티션 {worst_rec.metadata['partition']}의 lag "
                            f"{worst:,}건이 평균 {avg:,.0f}건의 "
                            f"{worst / avg:.1f}배입니다"
                        ),
                        evidence=[worst_rec.id],
                    )
                )

        return {"records": records, "metrics": metrics, "judgements": judgements}
