"""테스트 공용 도구.

핵심은 `temp_config()`다. 저장소의 `config/`를 건드리지 않고 임시 디렉터리에
3단 계층을 만들어 준다. 테스트가 실제 config를 수정하면 다른 테스트와
개발 환경이 함께 망가진다.
"""

from __future__ import annotations

import asyncio
import copy
import json
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.application.graph.builder import build_dependencies, build_graph
from src.application.graph.state import ReportState
from src.config.env import EnvConfig
from src.config.loader import DeployConfig
from src.constants import CONFIG_ROOT
from src.domain.models import BaseContext

#: 모든 테스트가 쓰는 고정 시각. as_of가 데이터를 결정하므로 값이 재현된다.
AS_OF = datetime(2026, 8, 13, 8, 0)
GBM = "mx"
FACTORY = "gumi"


def base_config() -> dict:
    """저장소의 실제 gbm config를 읽어온다.

    실제 파일을 출발점으로 삼아야 config 스키마가 바뀔 때 테스트도 함께
    깨진다. 테스트 안에 config를 하드코딩하면 그 신호를 잃는다.
    """
    return json.loads((CONFIG_ROOT / "gbm" / f"{GBM}.json").read_text(encoding="utf-8"))


@contextmanager
def temp_config(
    gbm: dict | None = None,
    factory_common: dict | None = None,
    factory_gbm: dict | None = None,
):
    """임시 3단 config를 만들고 DeployConfig를 넘긴다.

    None인 계층은 파일을 만들지 않는다 — "파일이 없는 계층"도 테스트 대상이다.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if gbm is not None:
            (root / "gbm").mkdir(parents=True, exist_ok=True)
            (root / "gbm" / f"{GBM}.json").write_text(
                json.dumps(gbm, ensure_ascii=False), encoding="utf-8"
            )
        fac = root / "factories" / FACTORY
        for name, payload in (("common", factory_common), (f"{GBM}", factory_gbm)):
            if payload is None:
                continue
            fac.mkdir(parents=True, exist_ok=True)
            (fac / f"{name}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        yield DeployConfig(GBM, FACTORY, root=root)


def with_subgraph_patch(patch: Callable[[dict], Any]) -> dict:
    """실제 config를 복사해 subgraphs만 손본 dict를 돌려준다.

    발송은 꺼둔다 — 테스트가 파일을 쓰거나 메일을 보내면 안 된다.
    """
    cfg = base_config()
    cfg.setdefault("delivery", {}).setdefault("file", {})["enabled"] = False
    cfg.setdefault("delivery", {}).setdefault("mail", {})["enabled"] = False
    patch(cfg)
    return cfg


def run_graph(cfg: DeployConfig, as_of: datetime = AS_OF) -> dict:
    """그래프를 끝까지 돌리고 최종 State를 돌려준다."""
    env = EnvConfig()
    deps = build_dependencies(cfg, env)
    graph = build_graph(cfg, deps, env)
    ctx = BaseContext(as_of=as_of, gbm=GBM, factory=FACTORY)
    return asyncio.run(graph.ainvoke(ReportState(ctx=ctx)))


def section(state: dict, key: str):
    """State에서 특정 서브그래프의 섹션을 꺼낸다."""
    for s in state["sections"]:
        if s.key == key:
            return s
    raise AssertionError(f"섹션 '{key}'이 없습니다. 있는 것: {[s.key for s in state['sections']]}")


def deep_copy(payload: dict) -> dict:
    return copy.deepcopy(payload)
