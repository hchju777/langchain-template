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
from src.application.subgraphs.process_graph import Probe, ProcessGraph
from src.domain.models import (
    Judgement,
    Metric,
    ProbeDecision,
    Record,
    Severity,
    SnapshotContext,
)
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


class StubProbe(Probe):
    """단일 노드 probe. 여러 노드여도 되지만 여기서는 최소로 둔다."""

    def __init__(self, name, kinds, marker=None, boom=False):
        self.name = name
        self.kinds = kinds
        self.description = f"{name} 확인"
        self._marker = marker or f"{name}-rec"
        self._boom = boom

    def compile(self, deps, config):
        async def step(state: SubgraphState) -> dict:
            if self._boom:
                raise RuntimeError("probe 조회 실패")
            return {"probe_records": [*state.probe_records, Record(id=self._marker)]}

        g = StateGraph(SubgraphState)
        g.add_node("step", step)
        g.add_edge(START, "step")
        g.add_edge("step", END)
        return g.compile()


class ProbingSubgraph(BaseSubgraph):
    registry_name = "test.probing"
    title = "probe 있는 분석"
    required_kinds = ("kpi", "alarms", "equipment_status")
    probes = ()

    async def fetch(self, state: SubgraphState) -> dict:
        return {"records": [Record(id="base-1")]}

    async def compute(self, state: SubgraphState) -> dict:
        return {"metrics": [Metric(name="지표", value=len(state.records))]}

    async def judge(self, state: SubgraphState) -> dict:
        seen = state.records + state.probe_records
        return {"judgements": [Judgement(
            subject=f"판정 {len(seen)}건", severity=Severity.WARNING,
            reasoning="…", evidence=[r.id for r in seen])]}

    def build_process(self):
        return ProcessGraph(self, self.probes).compile()


def run_probing(probes, max_rounds, llm=None):
    cls = type("Sub", (ProbingSubgraph,), {"probes": probes})
    sub = cls(SubgraphConfig(enabled=True, max_probe_rounds=max_rounds),
              Dependencies(llm=llm or FakeLLMAdapter(model="fake-local", seed="t")))
    compiled = sub.compile()
    return asyncio.run(compiled.ainvoke(SubgraphState(ctx=CTX, scoped=CTX)))


class ProcessGraphTest(unittest.TestCase):
    def test_probe_kinds_must_be_declared(self):
        """선언하지 않은 데이터는 열 수 없다. 조립 시점에 막는다."""
        sub = ProbingSubgraph(SubgraphConfig(), Dependencies())
        with self.assertRaises(ValueError) as caught:
            ProcessGraph(sub, (StubProbe("rogue", ("material_stock",)),))
        self.assertIn("material_stock", str(caught.exception))

    def test_declared_kinds_are_accepted(self):
        sub = ProbingSubgraph(SubgraphConfig(), Dependencies())
        ProcessGraph(sub, (StubProbe("alarms", ("alarms",)),))  # 예외 없음

    def test_judgements_are_replaced_not_appended(self):
        """라운드마다 새로 만들어 덮어쓴다. 한 섹션에 모순된 판정이 남으면 안 된다."""
        out = run_probing((StubProbe("alarms", ("alarms",)),), max_rounds=1)
        self.assertEqual(len(out["judgements"]), 1)
        self.assertIn("2건", out["judgements"][0].subject)

    def test_probe_records_accumulate_without_duplicates(self):
        out = run_probing(
            (StubProbe("alarms", ("alarms",)),
             StubProbe("equipment", ("equipment_status",))),
            max_rounds=2,
        )
        self.assertEqual([r.id for r in out["probe_records"]],
                         ["alarms-rec", "equipment-rec"])

    def test_probe_evidence_survives_the_guardrail(self):
        """probe로 늘어난 record를 인용한 판정이 폐기되면 안 된다."""
        out = run_probing((StubProbe("alarms", ("alarms",)),), max_rounds=1)
        evidence = out["judgements"][0].evidence
        self.assertIn("alarms-rec", evidence)


class CountingLLM(FakeLLMAdapter):
    """decide 호출 횟수를 센다. 상한 판단이 코드에 있는지 보려는 것이다."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.decide_calls = 0

    async def _decide_raw(self, prompt, allowed):
        self.decide_calls += 1
        return await super()._decide_raw(prompt, allowed)


class ProbeCapTest(unittest.TestCase):
    def test_zero_rounds_never_calls_the_model(self):
        """기본값 0이면 probe가 안 돌고 LLM도 안 부른다."""
        llm = CountingLLM(model="fake-local", seed="t")
        out = run_probing((StubProbe("alarms", ("alarms",)),), max_rounds=0, llm=llm)
        self.assertEqual(llm.decide_calls, 0)
        self.assertEqual(out["probe_records"], [])
        self.assertEqual(out["probed"], [])

    def test_stops_at_the_cap_without_asking(self):
        """상한에 닿으면 LLM을 부르지 않고 끝낸다."""
        llm = CountingLLM(model="fake-local", seed="t")
        out = run_probing(
            (StubProbe("alarms", ("alarms",)),
             StubProbe("equipment", ("equipment_status",))),
            max_rounds=1,
            llm=llm,
        )
        self.assertEqual(len(out["probed"]), 1)
        # 라운드 0에서 한 번 묻고, 상한에 닿은 뒤로는 묻지 않는다.
        self.assertEqual(llm.decide_calls, 1)

    def test_exhausted_candidates_stop_without_asking(self):
        """후보를 다 돌면 상한이 남아도 끝낸다."""
        llm = CountingLLM(model="fake-local", seed="t")
        out = run_probing((StubProbe("alarms", ("alarms",)),), max_rounds=5, llm=llm)
        self.assertEqual(out["probed"], ["alarms"])
        self.assertEqual(llm.decide_calls, 1)

    def test_a_probe_is_never_offered_twice(self):
        out = run_probing(
            (StubProbe("alarms", ("alarms",)),
             StubProbe("equipment", ("equipment_status",))),
            max_rounds=5,
        )
        self.assertEqual(sorted(out["probed"]), ["alarms", "equipment"])
        self.assertEqual(len(out["probed"]), len(set(out["probed"])))

    def test_prompt_differs_between_rounds(self):
        """프롬프트가 같으면 replay가 같은 응답을 재생해 루프가 끝나지 않는다."""
        llm = FakeLLMAdapter(model="fake-local", seed="t")
        run_probing(
            (StubProbe("alarms", ("alarms",)),
             StubProbe("equipment", ("equipment_status",))),
            max_rounds=2,
            llm=llm,
        )
        prompts = [t.prompt for t in llm.drain_traces()]
        self.assertEqual(len(prompts), len(set(prompts)))
