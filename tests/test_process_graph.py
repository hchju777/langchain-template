"""process 슬롯 일반화.

build_process()를 구현하지 않으면 기존 process 메서드를 그대로 쓴다.
기존 6개 서브그래프가 전부 그 경로이므로, 이 변경으로 동작이 바뀌면 안 된다.
"""

from __future__ import annotations

import asyncio
import unittest

from langgraph.graph import END, START, StateGraph

from src.application.graph.state import Dependencies
from src.application.subgraphs.base import (
    BaseSubgraph,
    SubgraphConfig,
    SubgraphState,
)
from src.domain.models import Metric, ProbeDecision, Record, SnapshotContext
from src.infrastructure.llm import FakeLLMAdapter
from tests.helpers import AS_OF

CTX = SnapshotContext(as_of=AS_OF, gbm="mx", factory="gumi")


class MethodSubgraph(BaseSubgraph):
    """build_process를 구현하지 않는 기존 방식."""

    registry_name = "test.method"
    title = "메서드 방식"

    async def process(self, state: SubgraphState) -> dict:
        return {"records": [Record(id="r1")], "metrics": [Metric(name="m", value=1)]}


class NestedSubgraph(BaseSubgraph):
    """build_process로 중첩 그래프를 꽂는 방식."""

    registry_name = "test.nested"
    title = "중첩 그래프 방식"

    def build_process(self):
        async def step(state: SubgraphState) -> dict:
            return {
                "records": [Record(id="r1")],
                "metrics": [Metric(name="m", value=1)],
            }

        g = StateGraph(SubgraphState)
        g.add_node("step", step)
        g.add_edge(START, "step")
        g.add_edge("step", END)
        return g.compile()


def run(subgraph_cls):
    sub = subgraph_cls(
        SubgraphConfig(enabled=True),
        Dependencies(llm=FakeLLMAdapter(model="fake-local", seed="t")),
    )
    compiled = sub.compile()
    return asyncio.run(compiled.ainvoke(SubgraphState(ctx=CTX, scoped=CTX)))


class ProcessSlotTest(unittest.TestCase):
    def test_default_uses_the_process_method(self):
        deps = Dependencies(llm=FakeLLMAdapter(model="fake-local", seed="t"))
        self.assertIsNone(MethodSubgraph(SubgraphConfig(), deps).build_process())

    def test_nested_graph_produces_the_same_shape(self):
        """두 방식의 결과가 같아야 슬롯 교체가 안전하다."""
        from_method = run(MethodSubgraph)
        from_nested = run(NestedSubgraph)
        self.assertEqual(
            [r.id for r in from_method["records"]],
            [r.id for r in from_nested["records"]],
        )
        self.assertEqual(
            [m.name for m in from_method["metrics"]],
            [m.name for m in from_nested["metrics"]],
        )

    def test_nested_graph_does_not_leak_input_fields(self):
        """ainvoke는 ctx·scoped까지 돌려준다. 그것을 바깥 update로 올리면
        나중에 리듀서가 붙는 순간 조용히 중복이 생긴다."""
        from src.application.subgraphs.base import _PROCESS_OUTPUT

        self.assertNotIn("ctx", _PROCESS_OUTPUT)
        self.assertNotIn("scoped", _PROCESS_OUTPUT)

    def test_probe_fields_default_empty(self):
        state = SubgraphState(ctx=CTX)
        self.assertEqual(state.probe_records, [])
        self.assertEqual(state.probe_rounds, 0)
        self.assertEqual(state.probed, [])

    def test_probe_rounds_default_is_zero(self):
        """기본이 0이라 config에서 올리지 않으면 probe가 아예 안 돈다."""
        self.assertEqual(SubgraphConfig().max_probe_rounds, 0)


DONE = "done"


class DecideTest(unittest.TestCase):
    """분기 선택도 코드가 대조한다. 잘못된 선택은 계속 파는 쪽이 아니라
    멈추는 쪽으로 넘어져야 한다."""

    def decide(self, allowed):
        llm = FakeLLMAdapter(model="fake-local", seed="test")
        decision = asyncio.run(llm.decide("test.node", "라운드 0 · 이미 돈 것 없음", allowed))
        return decision, llm

    def test_picks_from_the_allowed_list(self):
        decision, _ = self.decide(["alarms", "equipment", DONE])
        self.assertIn(decision.next_step, ["alarms", "equipment", DONE])

    def test_unknown_choice_becomes_done(self):
        class Rogue(FakeLLMAdapter):
            async def _decide_raw(self, prompt, allowed):
                return ProbeDecision(next_step="nonexistent.probe", reason="…")

        llm = Rogue(model="fake-local")
        decision = asyncio.run(llm.decide("test.node", "프롬프트", ["alarms", DONE]))
        self.assertEqual(decision.next_step, DONE)
        self.assertTrue(any("nonexistent.probe" in d for d in llm.guardrail_drops))

    def test_records_a_trace(self):
        """replay가 되려면 프롬프트와 응답이 남아야 한다."""
        _, llm = self.decide(["alarms", DONE])
        traces = llm.drain_traces()
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].node, "test.node")

    def test_empty_allowed_list_is_done(self):
        decision, _ = self.decide([])
        self.assertEqual(decision.next_step, DONE)
