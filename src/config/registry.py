"""서브그래프·노드 레지스트리와 부팅 검증.

등록명은 기본적으로 **모듈의 상대 경로**에서 유도된다.

    src/application/subgraphs/kafka/lag.py  →  "kafka.lag"

그래서 config의 이름만 보면 파일 위치를 바로 알 수 있다. 다만 이 이름은
경로 문자열이 아니라 레지스트리 키다. 파일을 옮기거나 이름을 바꿀 때
@register("kafka.lag")로 고정하면 config를 건드리지 않아도 된다.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from src.constants import KEY_ENABLED, KEY_LLM, KEY_NODES, KEY_PORTS, KEY_SUBGRAPHS

SUBGRAPH_PACKAGE = "src.application.subgraphs"
NODE_PACKAGE = "src.application.nodes"

_subgraphs: dict[str, type] = {}
_nodes: dict[str, Callable] = {}
#: 독립 실행용이 아니라 슬롯 override 대상으로만 쓰이는 서브그래프.
#: 고아 검증에서 제외된다.
_slot_only: set[str] = set()
_discovered = False


class ConfigValidationError(Exception):
    """부팅 검증 실패. 모든 문제를 모아서 한 번에 보고한다."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        detail = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"설정 검증에 실패했습니다 ({len(problems)}건):\n{detail}")


def _derive_name(module: str, package_prefix: str) -> str:
    """모듈 경로에서 등록명을 유도한다."""
    prefix = package_prefix + "."
    if module.startswith(prefix):
        return module[len(prefix) :]
    return module


def register(
    name: str | None = None, *, slot_only: bool = False
) -> Callable[[type], type]:
    """서브그래프를 등록한다. 이름을 주지 않으면 모듈 경로가 곧 이름이다.

    slot_only=True는 "이 클래스는 config에 직접 등록되지 않고 슬롯 override
    대상으로만 쓰인다"는 선언이다. 공장별 특수화 클래스가 그렇다 — 구미
    전용 클래스는 아산 config에는 등장하지 않으므로, 이 표시가 없으면
    다른 공장에서 고아로 잡힌다.
    """

    def decorator(cls: type) -> type:
        key = name or _derive_name(cls.__module__, SUBGRAPH_PACKAGE)
        if key in _subgraphs and _subgraphs[key] is not cls:
            raise RuntimeError(f"서브그래프 이름이 중복되었습니다: {key}")
        _subgraphs[key] = cls
        if slot_only:
            _slot_only.add(key)
        cls.registry_name = key  # type: ignore[attr-defined]
        return cls

    return decorator


def register_node(name: str | None = None) -> Callable[[Callable], Callable]:
    """슬롯 부품(validate/process/output/error)을 등록한다."""

    def decorator(fn: Callable) -> Callable:
        key = name or _derive_name(fn.__module__, NODE_PACKAGE) + f".{fn.__name__}"
        _nodes[key] = fn
        return fn

    return decorator


def _walk(package: str) -> None:
    try:
        pkg = importlib.import_module(package)
    except ModuleNotFoundError:
        return
    for info in pkgutil.walk_packages(pkg.__path__, prefix=package + "."):
        if info.ispkg:
            continue
        importlib.import_module(info.name)


def discover(force: bool = False) -> None:
    """패키지를 재귀 스캔해 import한다. 데코레이터가 등록을 수행한다."""
    global _discovered
    if _discovered and not force:
        return
    _walk(SUBGRAPH_PACKAGE)
    _walk(NODE_PACKAGE)
    _discovered = True


def get_subgraph(name: str) -> type:
    discover()
    return _subgraphs[name]


def get_node(name: str) -> Callable:
    discover()
    return _nodes[name]


SLOT_KIND_NODE = "node"
SLOT_KIND_SUBGRAPH = "subgraph"


def slot_source(name: str) -> tuple[str, Any]:
    """슬롯 override 대상을 찾는다. 두 가지를 받는다.

        "outputs.no_llm"        공유 노드 부품  → ("node", 함수)
        "material.stock_gumi"   다른 서브그래프 → ("subgraph", 클래스)

    후자가 "상속은 코드에서, 선택은 config에서"를 가능하게 한다. 조립할 때
    그 클래스로 인스턴스를 따로 만들어 슬롯을 가져오는데, 메서드만 빌려오면
    그 클래스의 다른 멤버(헬퍼 메서드·상수)에 접근할 수 없기 때문이다.
    """
    discover()
    if name in _nodes:
        return SLOT_KIND_NODE, _nodes[name]
    if name in _subgraphs:
        return SLOT_KIND_SUBGRAPH, _subgraphs[name]
    raise KeyError(f"슬롯 대상 '{name}'을 찾을 수 없습니다")


def slot_candidates() -> list[str]:
    """슬롯에 쓸 수 있는 이름 전부. 에러 메시지용."""
    discover()
    return sorted([*_nodes, *_subgraphs])


def all_subgraphs() -> dict[str, type]:
    discover()
    return dict(_subgraphs)


def is_slot_only(name: str) -> bool:
    discover()
    return name in _slot_only


def all_nodes() -> dict[str, Callable]:
    discover()
    return dict(_nodes)


def validate_config(
    subgraph_config: dict[str, Any], routed_kinds: set[str] | None = None
) -> dict[str, BaseModel]:
    """부팅 검증. 하나라도 실패하면 프로세스를 띄우지 않는다.

    1. 이름 대조   — config의 이름이 레지스트리에 있는가 / 레지스트리 고아가 있는가
    2. 스키마 검증 — 각 서브그래프 config가 자기 BaseModel을 만족하는가
    3. 참조 무결성 — nodes 슬롯 override가 가리키는 이름이 실재하는가
    4. 데이터 경로 — 서브그래프가 쓰는 kind가 config의 ports에 매핑돼 있는가
                     (routed_kinds를 넘겼을 때만)

    에러를 하나씩 고치며 재시작하는 것은 3단 merge 환경에서 고통스러우므로
    모든 문제를 모아서 한 번에 던진다.
    """
    discover()
    problems: list[str] = []
    parsed: dict[str, BaseModel] = {}

    # 슬롯 override 대상으로 쓰인 이름은 config에 직접 없어도 고아가 아니다.
    # 상속 클래스는 보통 그렇게만 쓰인다.
    slot_targets = {
        target
        for raw in subgraph_config.values()
        for target in (raw.get(KEY_NODES) or {}).values()
    }

    # 1. 이름 대조
    for name in subgraph_config:
        if name not in _subgraphs:
            candidates = ", ".join(sorted(_subgraphs)) or "(등록된 것 없음)"
            problems.append(
                f"config의 '{KEY_SUBGRAPHS}.{name}'이 레지스트리에 없습니다. "
                f"등록된 서브그래프: {candidates}"
            )
    for name in _subgraphs:
        if name in _slot_only or name in slot_targets:
            continue  # 슬롯 부품 전용이거나 실제로 슬롯에 쓰이는 중
        if name not in subgraph_config:
            problems.append(
                f"서브그래프 '{name}'이 등록되었지만 어느 config에도 없습니다 "
                f"(고아). config에 추가하거나, 슬롯 전용이면 "
                f"@register(slot_only=True)를 쓰거나, 파일을 지우세요."
            )

    for name, raw in subgraph_config.items():
        cls = _subgraphs.get(name)
        if cls is None:
            continue

        # 2. 스키마 검증
        model_cls: type[BaseModel] = getattr(cls, "config_model", None)  # type: ignore[assignment]
        if model_cls is None:
            problems.append(f"서브그래프 '{name}'에 config_model이 없습니다.")
            continue
        # nodes(슬롯 override)와 llm(모델 override)은 서브그래프 스키마가 아니라
        # 조립 지시라서 config_model 검증에서 뺀다.
        payload = {k: v for k, v in raw.items() if k not in (KEY_NODES, KEY_LLM)}
        try:
            parsed[name] = model_cls(**payload)
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"])
                problems.append(
                    f"'{KEY_SUBGRAPHS}.{name}.{loc}': {err['msg']} "
                    f"(입력: {err.get('input')!r})"
                )

        # 3. 참조 무결성 — 노드 부품과 서브그래프 슬롯 양쪽을 본다
        for slot, node_name in (raw.get(KEY_NODES) or {}).items():
            if node_name not in _nodes and node_name not in _subgraphs:
                available = ", ".join(slot_candidates()) or "(등록된 것 없음)"
                problems.append(
                    f"'{KEY_SUBGRAPHS}.{name}.{KEY_NODES}.{slot}'가 가리키는 "
                    f"'{node_name}'이 없습니다. 쓸 수 있는 이름: {available}"
                )

        # 4. 데이터 경로 — 켜져 있는 서브그래프만 본다. 꺼둔 분석의 kind는
        #    ports에 없어도 상관없다.
        if routed_kinds is not None and raw.get(KEY_ENABLED):
            # 슬롯 override로 다른 서브그래프가 대신 돌면 그쪽 kind도 필요하다.
            needed = set(getattr(cls, "required_kinds", ()))
            for target in (raw.get(KEY_NODES) or {}).values():
                donor = _subgraphs.get(target)
                if donor is not None:
                    needed |= set(getattr(donor, "required_kinds", ()))
            for kind in sorted(needed - routed_kinds):
                mapped = ", ".join(sorted(routed_kinds)) or "(비어 있음)"
                problems.append(
                    f"서브그래프 '{name}'이 요청하는 '{kind}'을(를) 어디서 "
                    f"가져올지 '{KEY_PORTS}'에 없습니다. 매핑된 kind: {mapped}"
                )

    if problems:
        raise ConfigValidationError(problems)
    return parsed


def enabled_subgraphs(subgraph_config: dict[str, Any]) -> list[str]:
    return [
        name
        for name, raw in subgraph_config.items()
        if raw.get(KEY_ENABLED, False)
    ]
