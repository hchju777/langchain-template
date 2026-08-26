"""도메인 모델. 외부 기술을 하나도 모른다."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# 실행 컨텍스트
# --------------------------------------------------------------------------
class BaseContext(BaseModel):
    """모든 실행이 들고 다니는 기준. as_of는 반드시 밖에서 주입된다.

    노드 안에서 datetime.now()를 부르면 재개(Durable Execution)와
    Time Travel, 멱등성이 모두 깨진다.
    """

    as_of: datetime
    gbm: str
    factory: str
    #: 사람이 준 질의. 스케줄러 실행에서는 None이다.
    query: str | None = None


class SnapshotContext(BaseContext):
    """as_of 시점의 현재 상태를 보는 분석."""


class HistoricalContext(BaseContext):
    """구간을 보는 분석. start/end는 as_of와 window로부터 계산된다."""

    start_dt: datetime
    end_dt: datetime

    @classmethod
    def from_window(cls, ctx: BaseContext, window: timedelta) -> HistoricalContext:
        return cls(
            as_of=ctx.as_of,
            gbm=ctx.gbm,
            factory=ctx.factory,
            start_dt=ctx.as_of - window,
            end_dt=ctx.as_of,
        )


# --------------------------------------------------------------------------
# 수집 결과
# --------------------------------------------------------------------------
class Record(BaseModel):
    """수집 경계를 넘어온 데이터의 공통 형태.

    metadata에 식별·분류 정보를, record에 실제 값을 담는다.
    어댑터가 각자의 원본을 이 형태로 변환한다.
    """

    id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    record: dict[str, Any] = Field(default_factory=dict)


class FetchSpec(BaseModel):
    """포트에 무엇을 달라고 요청하는지. 어댑터가 해석한다."""

    kind: str
    filters: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# 판정
# --------------------------------------------------------------------------
class Severity(IntEnum):
    NORMAL = 0
    WARNING = 1
    CRITICAL = 2

    @property
    def label(self) -> str:
        return {0: "정상", 1: "경고", 2: "심각"}[int(self)]


class Judgement(BaseModel):
    """판정에는 항상 근거가 따라붙는다.

    evidence는 실제 입력 Record의 id를 가리켜야 한다. 이 규약 덕분에
    리포트의 문장을 원본까지 역추적할 수 있고, LLM 환각도 잡힌다.
    """

    subject: str
    severity: Severity
    reasoning: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 1.0


class Metric(BaseModel):
    """리포트 표에 그대로 렌더링되는 숫자. LLM이 다시 쓰지 않는다."""

    name: str
    value: float | int | str
    unit: str = ""
    note: str = ""


# --------------------------------------------------------------------------
# 리포트
# --------------------------------------------------------------------------
class ReportSection(BaseModel):
    """모든 서브그래프가 이 형태를 반환하므로 템플릿은 서브그래프를 모른다."""

    key: str
    title: str
    severity: Severity = Severity.NORMAL
    narrative: str = ""
    metrics: list[Metric] = Field(default_factory=list)
    judgements: list[Judgement] = Field(default_factory=list)
    degraded: bool = False


class OverallSummary(BaseModel):
    narrative: str
    counts: dict[str, int] = Field(default_factory=dict)
    top_issues: list[str] = Field(default_factory=list)


class SubgraphError(BaseModel):
    key: str
    slot: str
    message: str


class LLMTrace(BaseModel):
    """변수 치환이 끝난 최종 프롬프트와 raw 응답을 남긴다.

    State에 쌓이므로 체크포인터에 자동 저장되고, 별도 로깅 인프라가 없다.
    """

    node: str
    prompt: str
    response: str
    model: str
    temperature: float
    replayed: bool = False


class DeliveryRecord(BaseModel):
    """멱등키. 재개 시 State와 함께 복원되어 중복 발송을 막는다."""

    channel: str
    key: str
    target: str
