"""취합 단계의 배경 문서 반영과 인용 사후 검사.

취합 서술은 자유 텍스트라 judge()의 근거 강제가 걸리지 않는다. 그래서
대괄호 인용을 코드로 대조한다. LLM 없이 도는 결정론적 검사다.
"""

from __future__ import annotations

import asyncio
import unittest

from src.application.graph.aggregate import _unknown_citations, make_aggregate
from src.application.graph.state import Dependencies, ReportState
from src.domain.models import (
    BaseContext,
    Judgement,
    ReferenceDoc,
    ReportSection,
    Requirement,
    Severity,
)
from src.infrastructure.llm import FakeLLMAdapter
from tests.helpers import AS_OF

CTX = BaseContext(as_of=AS_OF, gbm="mx", factory="gumi")


class Refs:
    """참고 문서 포트의 가짜 구현. 파일을 읽지 않는다."""

    def __init__(self, docs):
        self.docs = docs

    async def load(self, ctx, requirement):
        return list(self.docs)


class Cites(FakeLLMAdapter):
    """정해진 문장을 그대로 서술로 내놓는다. 인용 검사를 겨냥한 것이다."""

    text = ""

    async def _complete(self, prompt):
        return self.text


def _state(**kw):
    section = ReportSection(
        key="kpi.check",
        title="KPI 점검",
        severity=Severity.WARNING,
        judgements=[
            Judgement(subject="수율 미달", severity=Severity.WARNING, reasoning="…")
        ],
    )
    return ReportState(ctx=CTX, sections=[section], **kw)


def _run(docs=(), llm=None, state=None):
    deps = Dependencies(
        llm=llm or FakeLLMAdapter(model="fake-local", seed="t"), references=Refs(docs)
    )
    return asyncio.run(make_aggregate(deps)(state or _state()))


def _citing(text):
    llm = Cites(model="fake-local")
    llm.text = text
    return llm


class CitationCheckTest(unittest.TestCase):
    def test_flags_only_unlisted_ids(self):
        allowed = {"kpi.check", "sop-01"}
        self.assertEqual(_unknown_citations("[kpi.check] 와 [sop-01] 참고", allowed), [])
        self.assertEqual(_unknown_citations("[sop-09] 에 따르면", allowed), ["sop-09"])

    def test_uppercase_leading_id_is_inspected(self):
        """"[SOP-99]"가 조용히 통과하면 결정론적이라던 검사가 거짓말이 된다."""
        self.assertEqual(
            _unknown_citations("[SOP-99] 에 따르면", {"sop-01"}), ["SOP-99"]
        )
        self.assertEqual(_unknown_citations("[SOP-01] 참고", {"SOP-01"}), [])

    def test_multiple_ids_in_one_bracket_are_inspected_individually(self):
        """한 괄호에 몰아 쓴 id 목록이 통째로 빠져나가면 안 된다."""
        self.assertEqual(
            _unknown_citations("[sop-99, sop-98] 참고", {"sop-01"}),
            ["sop-98", "sop-99"],
        )
        self.assertEqual(
            _unknown_citations("[sop-01, sop-99] 참고", {"sop-01"}), ["sop-99"]
        )
        self.assertEqual(
            _unknown_citations("[sop-01 sop-02] 참고", {"sop-01", "sop-02"}), []
        )

    def test_korean_bracket_labels_are_not_citations(self):
        """top_issues의 "[심각]" 같은 강조가 인용 검사에 끌려오면 안 된다."""
        for label in ("[심각]", "[경고]", "[정상]", "[A라인] 점검", "[심각] 수율 미달"):
            self.assertEqual(
                _unknown_citations(label, set()), [], f"{label}은 인용이 아니다"
            )

    def test_unknown_citation_is_recorded_as_guardrail_drop(self):
        out = _run(llm=_citing("[sop-99] 에 따르면 조치가 필요합니다."))
        self.assertTrue(any("sop-99" in d for d in out["guardrail_drops"]))

    def test_valid_citation_is_not_flagged(self):
        docs = [ReferenceDoc(id="sop-01", source="s", title="기준", content="본문")]
        out = _run(docs=docs, llm=_citing("[sop-01] 기준에 비추어 정상입니다."))
        self.assertEqual(out["guardrail_drops"], [])

    def test_narrative_is_kept_even_when_citation_is_unknown(self):
        """취합 서술은 판정이 아니라 요약이라 폐기 대상이 아니다."""
        out = _run(llm=_citing("[sop-99] 에 따르면 조치가 필요합니다."))
        self.assertIn("조치가 필요합니다", out["overall"].narrative)


class AggregateReferenceTest(unittest.TestCase):
    def test_documents_reach_the_prompt_with_ids(self):
        docs = [
            ReferenceDoc(
                id="sop-01", source="s", title="정기점검 기준", content="A라인 점검 중"
            )
        ]
        prompt = _run(docs=docs)["traces"][0].prompt
        self.assertIn("[sop-01]", prompt)
        self.assertIn("A라인 점검 중", prompt)

    def test_documents_land_in_state(self):
        docs = [ReferenceDoc(id="sop-01", source="s", title="기준", content="본문")]
        out = _run(docs=docs)
        self.assertEqual([d.id for d in out["references"]], ["sop-01"])

    def test_focus_reaches_the_prompt(self):
        out = _run(
            state=_state(requirement=Requirement(query="재고", focus=["재고", "소진"]))
        )
        self.assertIn("재고, 소진", out["traces"][0].prompt)
