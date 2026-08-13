"""체크포인터 — Durable Execution과 Time Travel의 토대.

노드가 끝날 때마다 State가 저장되므로 다음이 따라온다.

    재개        중단된 지점부터 다시 (이미 끝난 노드는 재실행하지 않음)
    Time Travel 과거 실행의 각 시점 State를 열람
    fork        특정 시점으로 돌아가 값을 바꿔 다시 실행
    멱등 발송    delivered가 State에 있으므로 재개해도 중복 발송이 없음

config로 고른다:

    { "checkpoint": { "backend": "memory" } }   # 기본. 프로세스 안에서만 유지
    { "checkpoint": { "backend": "mongodb" } }  # 영속. 재시작해도 재개 가능
    { "checkpoint": { "backend": "none" } }     # 저장 안 함

memory는 Time Travel과 fork가 완전히 동작하지만 프로세스가 죽으면 사라진다.
mongodb는 재시작 후 재개까지 되고, 접속 정보는 .env에서 온다.
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

#: 체크포인터 연결 확인 제한시간. 부팅이 오래 매달리지 않게 짧게 둔다.
CONNECT_TIMEOUT_MS = 3000

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


def _mongodb_saver(env: Any, extra_types: tuple[type, ...]):
    """영속 체크포인터. 프로세스가 재시작해도 중단 지점부터 재개된다.

    접속 정보는 config가 아니라 .env에서 온다 — 환경(dev/stg/prod)마다
    다른 축이기 때문이다.
    """
    try:
        from langgraph.checkpoint.mongodb import MongoDBSaver
        from pymongo import MongoClient
    except ImportError as exc:  # pragma: no cover - 설치 여부에 달림
        raise CheckpointerUnavailableError(
            "mongodb 체크포인터는 langgraph-checkpoint-mongodb 패키지가 필요합니다.\n"
            "  pip install langgraph-checkpoint-mongodb\n"
            f"당장은 '{BACKEND_MEMORY}'를 쓰세요 — 프로세스 안에서 Time Travel과 "
            "fork가 동작하고, 프로세스 재시작 후 재개만 불가능합니다."
        ) from exc

    uri = getattr(env, "mongodb_uri", "") if env is not None else ""
    if not uri:
        raise CheckpointerUnavailableError(
            "checkpoint.backend가 'mongodb'인데 접속 주소가 없습니다. "
            ".env에 MONGODB_URI를 넣으세요 (예: mongodb://mongo.internal:27017)."
        )

    # MongoDBSaver는 동기 MongoClient를 받지만 async 메서드를 제공한다.
    # 그래프가 ainvoke로 도는 데 문제가 없다.
    #
    # 생성자가 인덱스를 만들려고 **즉시 연결**한다(lazy가 아니다). 그래서
    # 서버가 없으면 여기서 실패하는데, 그게 맞는 동작이다 — 체크포인터를
    # 쓰겠다고 해놓고 저장이 안 되는 채로 도는 것보다 낫다. 다만 pymongo의
    # raw 예외를 그대로 올리면 원인을 알기 어려워 감싼다.
    from pymongo.errors import PyMongoError

    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=CONNECT_TIMEOUT_MS)
        return MongoDBSaver(
            client,
            db_name=getattr(env, "checkpoint_database", "langgraph"),
            serde=_serializer(extra_types),
        )
    except PyMongoError as exc:
        raise CheckpointerUnavailableError(
            f"MongoDB에 연결할 수 없습니다: {uri}\n"
            f"  {type(exc).__name__}: {str(exc).splitlines()[0]}\n"
            "MONGODB_URI를 확인하거나, 서버 없이 개발 중이라면 "
            f"checkpoint.backend를 '{BACKEND_MEMORY}'로 두세요."
        ) from exc


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
        return _mongodb_saver(env, extra_types)

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
