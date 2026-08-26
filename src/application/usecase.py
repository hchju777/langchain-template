"""리포트 실행 유스케이스.

CLI와 스케줄러가 **같은 함수**를 부른다. 둘은 트리거 방식만 다를 뿐
하는 일이 같기 때문이다 — 이 파일이 그 사실을 코드로 못 박는다.

여기가 application인 이유: "리포트를 만든다"는 것 자체가 유스케이스다.
CLI(사람이 명령)도 스케줄러(시간이 트리거)도 이걸 호출하는 쪽일 뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.application.graph.builder import build_dependencies, build_graph
from src.application.graph.state import ReportState
from src.config.env import EnvConfig
from src.config.loader import DeployConfig
from src.constants import PROJECT_ROOT
from src.domain.models import BaseContext
from src.infrastructure.checkpoint import thread_id_for


def resolve_attachments(context_paths: list[str] | None) -> tuple[str, ...]:
    """--context 경로를 절대 경로로 풀어 정렬한다.

    식별자에 들어가는 목록과 실제로 읽히는 목록이 **같은 계산**에서 나와야
    한다. 둘이 갈라지면 첨부 문서가 리포트를 바꿨는데 식별자는 정규 실행과
    같은 상태가 되고, 그게 이 함수가 막으려는 사고다. CLI의 실행 락도 같은
    이유로 이걸 부른다.

    상대 경로 기준은 StaticReferenceAdapter와 같은 저장소 루트다. 실행
    디렉터리에 따라 같은 인자가 다른 문서를 가리키면 안 된다.
    """
    resolved = [
        str((path if path.is_absolute() else PROJECT_ROOT / path).resolve())
        for path in (Path(raw) for raw in context_paths or [])
    ]
    return tuple(sorted(resolved))


@dataclass
class ReportRun:
    """한 번의 실행 결과."""

    state: dict
    thread_id: str
    graph: Any
    run_config: dict

    @property
    def rendered(self) -> str:
        return self.state.get("rendered") or ""

    @property
    def sections(self) -> list:
        return self.state.get("sections", [])

    @property
    def errors(self) -> list:
        return self.state.get("errors", [])

    @property
    def delivered(self) -> list:
        return self.state.get("delivered", [])

    @property
    def traces(self) -> list:
        return self.state.get("traces", [])

    @property
    def guardrail_drops(self) -> list:
        return self.state.get("guardrail_drops", [])


async def run_report(
    gbm: str,
    factory: str,
    as_of: datetime,
    *,
    env: EnvConfig | None = None,
    replay: dict[str, str] | None = None,
    on_node: Any = None,
    config_root: Path | None = None,
    query: str | None = None,
    context_paths: list[str] | None = None,
) -> ReportRun:
    """조립 → 실행 → 결과.

    부팅 검증에서 실패하면 예외가 그대로 올라간다. 호출자(CLI/스케줄러)가
    각자 방식으로 보고한다 — CLI는 사람이 읽는 메시지로, 스케줄러는 로그로.

    on_node를 주면 노드가 끝날 때마다 이름으로 호출된다(진행 표시용).
    config_root는 테스트에서 임시 config를 쓰기 위한 것이고, 평소에는
    생략해 저장소의 config/를 읽는다.
    """
    env = env or EnvConfig()
    cfg = DeployConfig(gbm, factory, root=config_root)

    # 한 번만 푼다. 아래 세 곳(문서 적재·실행 컨텍스트·thread_id)이 같은
    # 목록을 봐야 "리포트를 바꾼 문서"와 "식별자에 든 문서"가 어긋나지 않는다.
    attachments = resolve_attachments(context_paths)

    deps = build_dependencies(cfg, env, replay=replay, context_paths=list(attachments))
    graph = build_graph(cfg, deps, env, replay=replay)

    ctx = BaseContext(
        as_of=as_of,
        gbm=gbm,
        factory=factory,
        query=query,
        attachments=attachments,
    )
    thread_id = thread_id_for(gbm, factory, as_of, query, attachments)
    run_config = {"configurable": {"thread_id": thread_id}}

    try:
        if on_node is None:
            state = await graph.ainvoke(ReportState(ctx=ctx), config=run_config)
        else:
            # updates로 진행을 알리면서 values로 최종 State를 받는다.
            # 그래프를 두 번 돌리면 발송이 두 번 나간다.
            state: dict = {}
            async for mode, chunk in graph.astream(
                ReportState(ctx=ctx),
                stream_mode=["updates", "values"],
                config=run_config,
            ):
                if mode == "updates":
                    for node in chunk:
                        on_node(node)
                else:
                    state = chunk
    finally:
        await deps.close()

    return ReportRun(
        state=state, thread_id=thread_id, graph=graph, run_config=run_config
    )
