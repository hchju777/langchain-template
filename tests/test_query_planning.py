"""질의 분석의 구조화 출력과 가드레일.

LLM이 없는 분석 이름을 지목해도 리포트가 정상 생성되어야 한다. 이 검사는
LLM 없이 도는 결정론적 대조라 항상 켜 둔다.
"""

from __future__ import annotations

import asyncio
import unittest

from src.application.graph.analyze import make_analyze_query
from src.application.graph.state import Dependencies, ReportState
from src.domain.models import BaseContext, Requirement
from src.infrastructure.llm import FakeLLMAdapter
from tests.helpers import AS_OF

ALLOWED = ["material.stock", "kpi.check", "line.equipment"]


def plan(prompt: str, allowed: list[str] | None = None):
    llm = FakeLLMAdapter(model="fake-local", seed="test")
    req = asyncio.run(llm.plan("analyze_query", prompt, ALLOWED if allowed is None else allowed))
    return req, llm


class SelectionGuardrailTest(unittest.TestCase):
    """검사기가 LLM이 아니라 집합 연산이므로 검사 자체가 틀릴 수 없다."""

    def test_keeps_only_registered_names(self):
        req, _ = plan("stock 상황을 알려줘")
        self.assertIn("material.stock", req.selected)
        for name in req.selected:
            self.assertIn(name, ALLOWED)

    def test_records_dropped_names(self):
        req, llm = plan("stock 상황을 알려줘")
        self.assertTrue(req.dropped, "Fake는 가드레일 시연용으로 없는 이름을 하나 섞는다")
        for name in req.dropped:
            self.assertNotIn(name, ALLOWED)
        self.assertTrue(any(req.dropped[0] in d for d in llm.guardrail_drops))

    def test_records_trace(self):
        """프롬프트와 응답이 남아야 replay가 가능하다."""
        _, llm = plan("stock 상황을 알려줘")
        traces = llm.drain_traces()
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].node, "analyze_query")
        self.assertIn("stock", traces[0].prompt)

    def test_full_scope_when_nothing_allowed(self):
        req, _ = plan("아무거나", allowed=[])
        self.assertEqual(req.selected, [])
        self.assertTrue(req.is_full_scope)

    def test_requirement_defaults_are_empty(self):
        req = Requirement()
        self.assertEqual(req.query, "")
        self.assertEqual(req.selected, [])
        self.assertFalse(req.is_full_scope)


TITLES = {"material.stock": "자재 소진 예상", "kpi.check": "KPI 점검",
          "line.equipment": "라인별 장비 상태"}


def run_node(query, llm=None):
    deps = Dependencies(llm=llm or FakeLLMAdapter(model="fake-local", seed="test"))
    node = make_analyze_query(deps, ALLOWED, TITLES)
    ctx = BaseContext(as_of=AS_OF, gbm="mx", factory="gumi", query=query)
    return asyncio.run(node(ReportState(ctx=ctx)))


class AnalyzeQueryNodeTest(unittest.TestCase):
    def test_no_query_selects_everything_without_calling_llm(self):
        """스케줄러 경로. LLM을 부르지 않아야 비용도 지연도 늘지 않는다."""
        out = run_node(None)
        req = out["requirement"]
        self.assertEqual(req.selected, ALLOWED)
        self.assertTrue(req.is_full_scope)
        self.assertFalse(out.get("traces"))

    def test_query_narrows_selection(self):
        out = run_node("stock 상황을 알려줘")
        req = out["requirement"]
        self.assertEqual(req.selected, ["material.stock"])
        self.assertEqual(req.query, "stock 상황을 알려줘")
        self.assertFalse(req.is_full_scope)

    def test_prompt_carries_titles(self):
        """등록명만으로는 LLM이 무슨 분석인지 알 수 없다."""
        out = run_node("stock 상황을 알려줘")
        self.assertTrue(out["traces"], "질의가 있으면 trace가 남아야 한다")
        self.assertIn("자재 소진 예상", out["traces"][0].prompt)

    def test_llm_failure_falls_back_to_full_scope(self):
        class Boom(FakeLLMAdapter):
            async def _plan_raw(self, prompt, allowed):
                raise RuntimeError("모델 응답 없음")

        out = run_node("stock 상황", llm=Boom(model="fake-local"))
        req = out["requirement"]
        self.assertEqual(req.selected, ALLOWED)
        self.assertTrue(req.is_full_scope)
        self.assertTrue(any("RuntimeError" in d for d in out["guardrail_drops"]))

    def test_empty_selection_falls_back_to_full_scope(self):
        class Empty(FakeLLMAdapter):
            async def _plan_raw(self, prompt, allowed):
                return Requirement(selected=["nonexistent.only"])

        out = run_node("무관한 질의", llm=Empty(model="fake-local"))
        self.assertEqual(out["requirement"].selected, ALLOWED)
        self.assertTrue(out["requirement"].is_full_scope)
