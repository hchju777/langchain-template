"""노드 데코레이터. 조립 시점에 붙는다.

비즈니스 로직과 인프라 관심사를 분리하고, 필요한 것만 골라 겹쳐 쓴다.
어느 노드에 무엇을 붙일지는 config가 정한다.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

NodeFn = Callable[[Any], Awaitable[Any]]


def with_timing(fn: NodeFn, label: str) -> NodeFn:
    async def wrapper(state: Any) -> Any:
        started = time.perf_counter()
        try:
            return await fn(state)
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            logger.info("node=%s elapsed_ms=%.1f", label, elapsed)

    return wrapper


def with_error_handling(fn: NodeFn, label: str) -> NodeFn:
    """서브그래프 바깥(취합·렌더·발송)에서 쓰는 최후 방어선.

    서브그래프 내부의 슬롯 실패는 handle_error가 이미 처리하므로 여기까지
    오지 않는다.
    """

    async def wrapper(state: Any) -> Any:
        try:
            return await fn(state)
        except Exception:
            logger.exception("node=%s 에서 처리되지 않은 예외", label)
            raise

    return wrapper


def with_cache(fn: NodeFn, label: str, ttl: int | None) -> NodeFn:
    """노드별 캐시. ttl이 None이면 아무것도 하지 않는다.

    replay 모드가 올라가는 자리이기도 하다 — 저장된 결과를 재생하면
    LLM을 고정한 채 후속 로직만 반복해 디버깅할 수 있다.
    """
    if ttl is None:
        return fn

    store: dict[str, tuple[float, Any]] = {}

    async def wrapper(state: Any) -> Any:
        key = f"{label}:{getattr(state, 'ctx', None)}"
        hit = store.get(key)
        now = time.time()
        if hit and now - hit[0] < ttl:
            logger.info("node=%s cache=hit", label)
            return hit[1]
        result = await fn(state)
        store[key] = (now, result)
        return result

    return wrapper
