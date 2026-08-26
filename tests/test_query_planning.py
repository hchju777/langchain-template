"""질의 분석의 구조화 출력과 가드레일.

LLM이 없는 분석 이름을 지목해도 리포트가 정상 생성되어야 한다. 이 검사는
LLM 없이 도는 결정론적 대조라 항상 켜 둔다.
"""

from __future__ import annotations

import asyncio
import unittest

from src.domain.models import Requirement
from src.infrastructure.llm import FakeLLMAdapter

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
