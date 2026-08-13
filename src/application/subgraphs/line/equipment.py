"""라인별 장비 상태 요약.

Redis에서 생산정보 / 라인 정보 / 장비 상태 세 갈래를 받아 라인 단위로
합친다. 세 번의 fetch가 같은 SnapshotContext(같은 as_of)를 쓰므로 서로
다른 시점을 섞어 보는 일이 없다.
"""

from __future__ import annotations

from collections import defaultdict

from src.application.subgraphs.base import BaseSubgraph, SubgraphConfig, SubgraphState
from src.config.registry import register
from src.domain.models import FetchSpec, Judgement, Metric, Severity, SnapshotContext

DOWN_STATES = ["DOWN", "ALARM"]


class LineEquipmentConfig(SubgraphConfig):
    down_states: list[str] = DOWN_STATES
    warn_down_ratio: float = 0.25       # 라인 장비 중 비가동 비율
    critical_down_ratio: float = 0.50
    achievement_warn_pct: float = 90.0  # 생산 달성률


@register()
class LineEquipment(BaseSubgraph):
    title = "라인별 장비 상태"
    config_model = LineEquipmentConfig
    context_type = SnapshotContext

    async def process(self, state: SubgraphState) -> dict:
        cfg: LineEquipmentConfig = self.config
        redis = self.deps.redis
        ctx = state.scoped

        production = await redis.fetch(ctx, FetchSpec(kind="production"))
        line_info = await redis.fetch(ctx, FetchSpec(kind="line_info"))
        equipment = await redis.fetch(ctx, FetchSpec(kind="equipment_status"))
        records = [*production, *line_info, *equipment]

        prod_by_line = {r.metadata["line"]: r for r in production}
        info_by_line = {r.metadata["line"]: r for r in line_info}
        equip_by_line: dict[str, list] = defaultdict(list)
        for rec in equipment:
            equip_by_line[rec.metadata["line"]].append(rec)

        metrics, judgements = [], []
        for line in sorted(equip_by_line):
            equips = equip_by_line[line]
            down = [e for e in equips if e.record["state"] in cfg.down_states]
            ratio = len(down) / len(equips)
            info = info_by_line.get(line)
            prod = prod_by_line.get(line)

            state_counts: dict[str, int] = defaultdict(int)
            for e in equips:
                state_counts[e.record["state"]] += 1
            breakdown = " / ".join(f"{k} {v}" for k, v in sorted(state_counts.items()))

            metrics.append(
                Metric(
                    name=f"{line} 장비",
                    value=f"{len(equips) - len(down)}/{len(equips)} 가동",
                    note=breakdown,
                )
            )
            if prod is not None:
                metrics.append(
                    Metric(
                        name=f"{line} 생산 달성률",
                        value=prod.record["achievement_pct"],
                        unit="%",
                        note=(
                            f"{prod.record['actual_qty']:,} / "
                            f"{prod.record['target_qty']:,}ea · "
                            f"수율 {prod.record['yield_pct']}%"
                        ),
                    )
                )

            if ratio >= cfg.critical_down_ratio:
                severity = Severity.CRITICAL
            elif ratio >= cfg.warn_down_ratio:
                severity = Severity.WARNING
            else:
                severity = Severity.NORMAL

            if severity is not Severity.NORMAL:
                names = ", ".join(e.metadata["equipment_id"] for e in down)
                model = info.record["model"] if info else "-"
                judgements.append(
                    Judgement(
                        subject=f"{line} 장비 비가동",
                        severity=severity,
                        reasoning=(
                            f"{len(equips)}대 중 {len(down)}대가 비가동 상태입니다 "
                            f"({ratio:.0%}, 생산모델 {model}): {names}"
                        ),
                        evidence=[e.id for e in down],
                    )
                )

            if prod is not None and prod.record["achievement_pct"] < cfg.achievement_warn_pct:
                judgements.append(
                    Judgement(
                        subject=f"{line} 생산 미달",
                        severity=Severity.WARNING,
                        reasoning=(
                            f"달성률 {prod.record['achievement_pct']}%로 기준 "
                            f"{cfg.achievement_warn_pct}%를 밑돕니다"
                        ),
                        evidence=[prod.id],
                    )
                )

        return {"records": records, "metrics": metrics, "judgements": judgements}
