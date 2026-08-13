"""어댑터 공통 정책: 타임아웃, 재시도, 커넥션 수명.

외부 시스템은 반드시 느려지거나 죽는다. 여기서 막지 않으면 8시 배치가
하루 종일 매달린다. 타임아웃 초과는 예외로 올라가 해당 서브그래프의
handle_error로 흘러가고, 나머지 분석은 정상 진행된다.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from src.constants import (
    DEFAULT_BACKOFF_BASE_SEC,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_SEC,
)
from src.domain.models import Record


class AdapterError(RuntimeError):
    """어댑터 경계에서 난 실패. 서브그래프의 handle_error가 받는다."""


class BaseAdapter:
    """모든 어댑터의 기반.

    실제 구현에서는 __init__에서 커넥션 풀을 만들고 close()에서 닫는다.
    풀은 프로세스 시작 시 한 번 만들어 Dependencies에 담는다. 노드가 매번
    연결을 열면 상주 스케줄러 환경에서 커넥션이 샌다.
    """

    name: str = "base"
    #: 이 어댑터가 다룰 줄 아는 kind. config의 ports가 여기 없는 kind를
    #: 이 어댑터로 보내면 **부팅 시** 막힌다. 선언하지 않으면 "저장소를
    #: 바꿨는데 실행 중에야 터지는" 상황이 생긴다.
    supported_kinds: tuple[str, ...] = ()

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        # 실제 구현 예시:
        # self._client = AsyncIOMotorClient(dsn, serverSelectionTimeoutMS=...)

    async def close(self) -> None:
        # 실제 구현 예시:
        # self._client.close()
        return None

    async def _call(self, fn: Callable[[], Awaitable[Any]]) -> Any:
        """타임아웃 + 지수 백오프 재시도를 씌워 호출한다."""
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await asyncio.wait_for(fn(), timeout=self.timeout)
            except asyncio.TimeoutError as exc:
                last = exc
            except Exception as exc:  # noqa: BLE001 - 어댑터 경계에서 감싼다
                last = exc
            if attempt < self.max_retries:
                await asyncio.sleep(DEFAULT_BACKOFF_BASE_SEC * (2**attempt))
        raise AdapterError(f"{self.name} 호출 실패: {last}") from last

    @staticmethod
    def _to_domain(raw: dict) -> Record:
        """원본 → 도메인 변환.

        어댑터의 사적인 구현 디테일이다. 별도 Mapper 계층이 아니다 —
        원본이 어떻게 생겼는지 아는 유일한 곳이 이 어댑터이기 때문이다.
        """
        return Record(
            id=raw["id"],
            metadata=raw.get("metadata", {}),
            record=raw.get("record", {}),
        )
