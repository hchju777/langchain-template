"""부모 그래프 State와 의존성 묶음."""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, Any

from pydantic import BaseModel, Field

from src.domain.models import (
    BaseContext,
    DeliveryRecord,
    LLMTrace,
    OverallSummary,
    ReportSection,
    SubgraphError,
)
from src.domain.reducers import merge_sections


class ReportState(BaseModel):
    ctx: BaseContext
    sections: Annotated[list[ReportSection], merge_sections] = Field(default_factory=list)
    errors: Annotated[list[SubgraphError], operator.add] = Field(default_factory=list)
    traces: Annotated[list[LLMTrace], operator.add] = Field(default_factory=list)
    #: 근거 검증에서 폐기된 판정. 서브그래프마다 LLM이 다를 수 있어 State로 모은다.
    guardrail_drops: Annotated[list[str], operator.add] = Field(default_factory=list)
    overall: OverallSummary | None = None
    rendered: str | None = None
    delivered: Annotated[list[DeliveryRecord], operator.add] = Field(default_factory=list)


@dataclass
class Dependencies:
    """프로세스 시작 시 한 번 만들어 조립 시점에 주입한다.

    노드가 매번 연결을 열면 상주 스케줄러 환경에서 커넥션이 샌다.
    """

    redis: Any = None
    mongo: Any = None
    kafka: Any = None
    rest: Any = None
    llm: Any = None
    renderer: Any = None
    health: dict[str, Any] = field(default_factory=dict)
    deliveries: list[Any] = field(default_factory=list)

    async def close(self) -> None:
        for adapter in (self.redis, self.mongo, self.kafka, self.rest):
            if adapter is not None and hasattr(adapter, "close"):
                await adapter.close()
