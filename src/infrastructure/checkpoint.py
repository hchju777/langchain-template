"""체크포인터 — Durable Execution과 Time Travel의 토대.

노드가 끝날 때마다 State가 저장되므로 다음이 따라온다.

    재개        중단된 지점부터 다시 (이미 끝난 노드는 재실행하지 않음)
    Time Travel 과거 실행의 각 시점 State를 열람
    fork        특정 시점으로 돌아가 값을 바꿔 다시 실행
    멱등 발송    delivered가 State에 있으므로 재개해도 중복 발송이 없음

config로 고른다:

    { "checkpoint": { "backend": "memory" } }

**지금은 memory만 실제로 동작합니다.** 이 환경에 langgraph-checkpoint-mongodb
패키지가 없어 설치할 수 없기 때문입니다. memory는 프로세스 안에서만 살아
있으므로 재시작 후 재개는 불가능하고, Time Travel과 fork는 같은 프로세스
안에서 완전히 동작합니다.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from src.domain import models as domain_models

#: config의 checkpoint.backend 값
BACKEND_NONE = "none"
BACKEND_MEMORY = "memory"
BACKEND_MONGODB = "mongodb"

#: 체크포인트에 저장되는 도메인 타입 전부. models.py에 새 모델을 추가하면
#: 자동으로 포함되므로 빠뜨릴 일이 없다. 명시하지 않으면 역직렬화가 막힌다.
DOMAIN_TYPES = tuple(
    obj
    for obj in vars(domain_models).values()
    if isinstance(obj, type) and obj.__module__ == domain_models.__name__
)


def _serializer(extra_types: tuple[type, ...] = ()) -> JsonPlusSerializer:
    """우리 Pydantic 모델을 복원할 수 있는 직렬화기.

    application의 State 타입은 여기서 import하지 않는다 — infrastructure가
    application을 알면 의존 방향이 뒤집힌다. 조립 시점에 주입받는다.
    """
    return JsonPlusSerializer(
        allowed_msgpack_modules=(*DOMAIN_TYPES, *extra_types)
    )


class CheckpointerUnavailableError(RuntimeError):
    """요청한 백엔드를 이 환경에서 쓸 수 없을 때."""


def build_checkpointer(
    cfg: dict, env: Any = None, extra_types: tuple[type, ...] = ()
) -> Any | None:
    """config → 체크포인터. None이면 그래프가 체크포인트 없이 돈다."""
    backend = cfg.get("backend", BACKEND_MEMORY)

    if backend == BACKEND_NONE:
        return None

    if backend == BACKEND_MEMORY:
        return InMemorySaver(serde=_serializer(extra_types))

    if backend == BACKEND_MONGODB:
        # 실제 구현:
        #   from langgraph.checkpoint.mongodb import MongoDBSaver
        #   from motor.motor_asyncio import AsyncIOMotorClient
        #   client = AsyncIOMotorClient(env.mongodb_uri, password=env.mongodb_password)
        #   return MongoDBSaver(client, db_name=env.checkpoint_database,
        #                       serde=_serializer(extra_types))
        #
        # 여기가 유일하게 바뀌는 지점이다. 그래프 조립도, 노드도, State도
        # 그대로다 — 체크포인터는 compile()에 넘기는 인자일 뿐이다.
        raise CheckpointerUnavailableError(
            "mongodb 체크포인터는 langgraph-checkpoint-mongodb 패키지가 필요합니다. "
            "이 환경에는 설치돼 있지 않습니다. "
            f"당장은 '{BACKEND_MEMORY}'를 쓰세요 — 프로세스 안에서 Time Travel과 "
            "fork가 동작하고, 프로세스 재시작 후 재개만 불가능합니다."
        )

    raise ValueError(
        f"알 수 없는 checkpoint.backend '{backend}'. "
        f"가능: {BACKEND_NONE}, {BACKEND_MEMORY}, {BACKEND_MONGODB}"
    )


def thread_id_for(gbm: str, factory: str, as_of) -> str:
    """실행 하나를 식별하는 키.

    같은 (gbm, factory, as_of)는 같은 스레드다. 그래서 중단된 실행을
    재개할 때 무엇을 이어받을지가 자명하고, 과거 실행을 날짜로 찾아갈 수
    있다. as_of를 밖에서 주입하기로 한 결정이 여기서도 값을 한다.
    """
    return f"{gbm}:{factory}:{as_of:%Y%m%dT%H%M}"
