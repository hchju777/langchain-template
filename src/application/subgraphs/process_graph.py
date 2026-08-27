"""process를 중첩 그래프로 엮는 곳.

    fetch → compute → judge → decide_next ─┬─ probe_a ─┐
                                 ↑          ├─ probe_b ─┤
                                 └──────────┴───────────┘
                                            └─ done → END

LLM은 **목적지 이름만** 고른다. 멈출 시점과 각 probe가 무엇을 조회할지는
코드가 쥔다. 오류 탐지는 모델이 약한 쪽이라, 그만둘 판단을 맡기지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.application.subgraphs.base import SubgraphState, _run_nested
from src.domain.models import SnapshotContext

logger = logging.getLogger(__name__)

DECIDE_NODE = "decide_next"
DONE = "done"


class Probe:
    """추가 조회 한 갈래. 자체가 여러 노드로 된 그래프일 수 있다.

    kinds가 이 probe가 열 수 있는 데이터 종류다. 조립 시점에 서브그래프의
    required_kinds 안에 있는지 검사하므로, 선언하지 않은 곳은 열 수 없다.
    """

    name: str = ""
    kinds: tuple[str, ...] = ()
    #: decide_next 프롬프트에 실린다. 이름만으로는 무엇을 보는지 알 수 없다.
    description: str = ""
    #: 이 probe가 요구하는 컨텍스트. 구간 데이터를 여는 probe는
    #: HistoricalContext를 선언한다.
    context_type: type = SnapshotContext

    def compile(self, deps: Any, config: Any):
        """SubgraphState 위에서 도는 컴파일된 그래프를 돌려준다.

        노드가 state를 받으므로 scoped도 state.scoped로 들어온다. as_of가
        인자로 조작되지 않는다는 뜻이고, 이것이 시점 재현성의 근거다.
        """
        raise NotImplementedError


def probe_guarded(name: str) -> Callable:
    """probe 실패를 삼키고 decide_next로 돌려보낸다.

    선택적 보강이 실패했다고 본체 판정을 버리는 것은 과하다. 다른 probe를
    고르거나 끝내면 되고, base 판정과 지표는 이미 State에 있다.
    """

    def decorator(fn: Callable) -> Callable:
        async def wrapper(state: SubgraphState) -> Command:
            try:
                update = await fn(state)
            except Exception as exc:  # noqa: BLE001 - probe 경계에서 격리한다
                logger.exception("probe=%s 실패", name)
                return Command(
                    goto=DECIDE_NODE,
                    update={"guardrail_drops": [
                        *state.guardrail_drops,
                        f"probe:{name} 실패로 건너뜁니다 ({type(exc).__name__})",
                    ]},
                )
            return Command(goto="judge", update=update)

        wrapper.__name__ = f"probe_{name}"
        return wrapper

    return decorator


class ProcessGraph:
    """서브그래프의 fetch/compute/judge와 probe들을 하나로 엮는다."""

    def __init__(self, subgraph: Any, probes: tuple[Probe, ...]) -> None:
        declared = set(subgraph.required_kinds)
        unknown = {k for p in probes for k in p.kinds} - declared
        if unknown:
            raise ValueError(
                f"{subgraph.registry_name}의 probe가 required_kinds에 없는 "
                f"{sorted(unknown)}을(를) 요청합니다. required_kinds에 선언하세요."
            )
        # kinds와 같은 자리에서 컨텍스트도 본다. 서브그래프가 스냅샷만
        # 들고 있는데 구간을 요구하는 probe를 달면, 그 probe는 매 실행
        # 조용히 실패한다 — 실패 격리가 있으니 아무도 눈치채지 못한다.
        for probe in probes:
            if not issubclass(subgraph.context_type, probe.context_type):
                raise ValueError(
                    f"{subgraph.registry_name}는 {subgraph.context_type.__name__}를 "
                    f"쓰는데 probe '{probe.name}'는 {probe.context_type.__name__}를 "
                    f"요구합니다."
                )
        self.subgraph = subgraph
        self.probes = probes

    # ------------------------------------------------------------------
    def _prompt(self, state: SubgraphState, remaining: list[str]) -> str:
        """라운드와 이미 판 곳을 싣는다.

        replay 키가 프롬프트 해시라, 라운드마다 프롬프트가 같으면 저장된
        응답이 반복 재생되어 루프가 끝나지 않는다.
        """
        catalog = "\n".join(
            f"- {p.name}: {p.description}" for p in self.probes if p.name in remaining
        )
        findings = "\n".join(f"- {j.subject}: {j.reasoning}" for j in state.judgements)
        return (
            f"[라운드 {state.probe_rounds}] 이미 확인한 것: "
            f"{', '.join(state.probed) or '없음'}\n\n"
            f"현재 판정:\n{findings}\n\n"
            f"추가로 확인할 수 있는 것:\n{catalog}\n\n"
            "판정을 확정하는 데 더 볼 것이 있으면 하나 고르고, 충분하면 "
            f"'{DONE}'을 고르세요."
        )

    async def decide_next(self, state: SubgraphState) -> Command:
        remaining = [p.name for p in self.probes if p.name not in state.probed]
        cap = self.subgraph.config.max_probe_rounds
        # 상한과 후보 소진은 LLM에게 묻지 않는다. 멈출 시점은 코드가 쥔다.
        if state.probe_rounds >= cap or not remaining:
            return Command(goto=END)

        decision = await self.subgraph.deps.llm.decide(
            self.subgraph.registry_name,
            self._prompt(state, remaining),
            [*remaining, DONE],
        )
        if decision.next_step == DONE:
            return Command(goto=END)

        return Command(
            goto=f"probe_{decision.next_step}",
            update={
                "probe_rounds": state.probe_rounds + 1,
                # 실패한 probe도 여기 남으므로 무한히 재시도하지 않는다.
                "probed": [*state.probed, decision.next_step],
            },
        )

    # ------------------------------------------------------------------
    def compile(self):
        sub = self.subgraph
        g = StateGraph(SubgraphState)
        g.add_node("fetch", sub.fetch)
        g.add_node("compute", sub.compute)
        g.add_node("judge", sub.judge)

        names = [f"probe_{p.name}" for p in self.probes]
        g.add_node(DECIDE_NODE, self.decide_next, destinations=(*names, END))
        for probe in self.probes:
            inner = _run_nested(probe.compile(sub.deps, sub.config))
            g.add_node(f"probe_{probe.name}",
                       probe_guarded(probe.name)(inner),
                       destinations=("judge", DECIDE_NODE))

        g.add_edge(START, "fetch")
        g.add_edge("fetch", "compute")
        g.add_edge("compute", "judge")
        g.add_edge("judge", DECIDE_NODE)
        return g.compile()
