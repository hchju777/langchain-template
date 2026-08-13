"""연결 상태 점검 — Redis / MongoDB / REST / Kafka가 붙는가.

등록명은 파일 경로에서 유도된다: health/connectivity.py → "health.connectivity"
"""

from __future__ import annotations

from src.application.subgraphs.base import BaseSubgraph, SubgraphConfig, SubgraphState
from src.config.registry import register
from src.domain.models import Judgement, Metric, Severity, SnapshotContext

DEFAULT_SOURCES = ["redis", "mongodb", "kafka", "rest"]


class ConnectivityConfig(SubgraphConfig):
    sources: list[str] = DEFAULT_SOURCES
    latency_warn_ms: float = 30.0


@register()
class Connectivity(BaseSubgraph):
    title = "연결 상태 점검"
    config_model = ConnectivityConfig
    context_type = SnapshotContext

    async def process(self, state: SubgraphState) -> dict:
        cfg: ConnectivityConfig = self.config
        records, metrics, judgements = [], [], []

        for name in cfg.sources:
            port = self.deps.health.get(name)
            if port is None:
                raise KeyError(f"health 포트에 '{name}'이 바인딩되지 않았습니다")

            rec = await port.ping(state.scoped)
            records.append(rec)

            reachable = bool(rec.record.get("reachable"))
            latency = rec.record.get("latency_ms")
            metrics.append(
                Metric(
                    name=name,
                    value="정상" if reachable else "실패",
                    note=(
                        f"{latency}ms" if reachable and latency is not None
                        else rec.record.get("detail", "")
                    ),
                )
            )

            # 판정은 임계치로 결정된다. 규칙으로 쓸 수 있으면 코드가 이긴다.
            if not reachable:
                judgements.append(
                    Judgement(
                        subject=f"{name} 연결 실패",
                        severity=Severity.CRITICAL,
                        reasoning=rec.record.get("detail", "연결할 수 없습니다"),
                        evidence=[rec.id],
                    )
                )
            elif latency is not None and latency > cfg.latency_warn_ms:
                judgements.append(
                    Judgement(
                        subject=f"{name} 응답 지연",
                        severity=Severity.WARNING,
                        reasoning=(
                            f"응답에 {latency}ms가 걸려 임계치 "
                            f"{cfg.latency_warn_ms}ms를 넘었습니다"
                        ),
                        evidence=[rec.id],
                    )
                )

        return {"records": records, "metrics": metrics, "judgements": judgements}
