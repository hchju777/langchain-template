"""포트 = 도메인이 선언하는 계약.

구현은 바깥 계층에 있다:
    infrastructure/  — 외부 시스템 I/O (DB, LLM, 메일, 파일)
    presentation/    — 사람이 읽는 형태로의 변환 (renderer)

포트는 기술이 아니라 역할로 나눈다. MongoPort/RedisPort처럼 기술 이름을
쓰면 서브그래프가 "나는 Mongo에서 읽는다"를 알게 되고, 저장소를 바꾸는
순간 서브그래프를 고쳐야 한다.

템플릿은 아래 몇 개만 레퍼런스로 제공한다. 각 프로젝트는 자기 도메인
포트를 여기에 추가하고 BaseAdapter를 상속해 구현하면 된다.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models import (
    BaseContext,
    FetchSpec,
    HistoricalContext,
    Judgement,
    Record,
    SnapshotContext,
)


@runtime_checkable
class HealthPort(Protocol):
    """연결 상태 점검. 모든 어댑터가 구현한다."""

    name: str

    async def ping(self, ctx: BaseContext) -> Record: ...


@runtime_checkable
class DataPort(Protocol):
    """서브그래프가 데이터를 요청하는 유일한 창구.

    `spec.kind`가 **무엇을 원하는지**를 말하고, 그것이 어느 저장소에서
    오는지는 config가 정한다. 그래서 서브그래프는 Redis인지 REST인지
    모르고, 저장소를 옮겨도 코드가 바뀌지 않는다.
    """

    async def fetch(self, ctx: BaseContext, spec: FetchSpec) -> list[Record]: ...


@runtime_checkable
class SnapshotPort(Protocol):
    """as_of 시점의 현재 값을 주는 어댑터가 만족하는 계약."""

    async def fetch(self, ctx: SnapshotContext, spec: FetchSpec) -> list[Record]: ...


@runtime_checkable
class HistoryPort(Protocol):
    """구간 조회. 시간 계약이 타입에 박혀 있다."""

    async def fetch(self, ctx: HistoricalContext, spec: FetchSpec) -> list[Record]: ...


@runtime_checkable
class LLMPort(Protocol):
    """모든 LLM 호출이 지나는 단일 경로.

    호출 → 기록 → structured output 파싱을 한곳에서 한다.
    replay 모드면 저장된 응답을 재생한다.
    """

    async def narrate(self, node: str, prompt: str) -> str: ...

    async def judge(
        self, node: str, prompt: str, allowed_ids: list[str]
    ) -> list[Judgement]: ...


@runtime_checkable
class ReportRendererPort(Protocol):
    """ReportSection 객체 → 문자열. 노드는 Jinja2도 md도 모른다."""

    def render(self, ctx: BaseContext, payload: dict) -> str: ...


@runtime_checkable
class DeliveryPort(Protocol):
    """발송 채널. 멱등키로 중복을 막는다."""

    channel: str

    async def deliver(self, key: str, content: str) -> str: ...
