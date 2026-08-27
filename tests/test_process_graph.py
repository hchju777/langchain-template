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
from src.domain.models import Metric, Record, SnapshotContext
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
