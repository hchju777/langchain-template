"""템플릿 파싱과 렌더링.

렌더러는 presentation이라 도메인 객체만 받는다. 여기 테스트에 State도
config도 등장하지 않는 것이 그 증거다.
"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.presentation.renderers import MarkdownRenderer, TemplateError, load_blocks
from src.domain.models import (
    BaseContext,
    Judgement,
    Metric,
    OverallSummary,
    ReportSection,
    Severity,
)

CTX = BaseContext(as_of=datetime(2026, 8, 13, 8, 0), gbm="mx", factory="gumi")


def write_template(body: str) -> tuple[Path, str]:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "t.md").write_text(body, encoding="utf-8")
    return tmp, "t.md"


def payload(sections=None, overall=None) -> dict:
    return {"sections": sections or [], "overall": overall}


class BlockParsingTest(unittest.TestCase):
    def test_블록_구분자로_나뉜다(self):
        tmp, name = write_template(
            "<!-- block: document -->\n본문\n<!-- block: section -->\n섹션\n"
        )
        blocks = load_blocks(tmp / name)
        self.assertEqual(set(blocks), {"document", "section"})

    def test_본문의_빈_줄이_보존된다(self):
        """구분자 줄만 제거해야 한다. 저자가 의도한 여백을 지우면 안 된다."""
        tmp, name = write_template("<!-- block: document -->\n\nA\n\nB\n")
        self.assertEqual(load_blocks(tmp / name)["document"], "\nA\n\nB\n")

    def test_일반_주석은_제거된다(self):
        tmp, name = write_template(
            "<!-- 사용법 설명 -->\n<!-- block: document -->\nX\n"
        )
        self.assertNotIn("사용법", load_blocks(tmp / name)["document"])

    def test_document_블록이_없으면_실패(self):
        tmp, name = write_template("<!-- block: section -->\nX\n")
        with self.assertRaises(TemplateError):
            load_blocks(tmp / name)

    def test_파일이_없으면_실패(self):
        with self.assertRaises(TemplateError):
            load_blocks(Path("/nonexistent/none.md"))


class RendererTest(unittest.TestCase):
    def test_없는_템플릿은_생성_시점에_터진다(self):
        """8시 배치가 아니라 부팅 때 알아야 한다."""
        with self.assertRaises(TemplateError):
            MarkdownRenderer(template="없는파일.md")

    def test_기본_템플릿으로_렌더링된다(self):
        r = MarkdownRenderer()
        out = r.render(CTX, payload([ReportSection(key="k", title="제목")]))
        self.assertIn("MX / GUMI", out)
        self.assertIn("제목", out)

    def test_숫자는_metrics에서_그대로_찍힌다(self):
        r = MarkdownRenderer()
        sec = ReportSection(
            key="k", title="T", metrics=[Metric(name="p95", value=412, unit="ms")]
        )
        out = r.render(CTX, payload([sec]))
        self.assertIn("412 ms", out)

    def test_심각도_순으로_정렬된다(self):
        r = MarkdownRenderer()
        out = r.render(
            CTX,
            payload(
                [
                    ReportSection(key="a", title="정상건", severity=Severity.NORMAL),
                    ReportSection(key="b", title="심각건", severity=Severity.CRITICAL),
                ]
            ),
        )
        self.assertLess(out.index("심각건"), out.index("정상건"))

    def test_부분_실패는_표시가_붙는다(self):
        r = MarkdownRenderer()
        out = r.render(CTX, payload([ReportSection(key="k", title="T", degraded=True)]))
        self.assertIn("부분 실패", out)

    def test_판정_근거가_실린다(self):
        r = MarkdownRenderer()
        sec = ReportSection(
            key="k",
            title="T",
            judgements=[
                Judgement(
                    subject="이상",
                    severity=Severity.WARNING,
                    reasoning="임계 초과",
                    evidence=["rec:1"],
                )
            ],
        )
        out = r.render(CTX, payload([sec]))
        self.assertIn("이상", out)
        self.assertIn("rec:1", out)

    def test_전체_요약의_집계는_코드가_센_값을_쓴다(self):
        r = MarkdownRenderer()
        overall = OverallSummary(
            narrative="요약", counts={"critical": 3, "warning": 2, "normal": 0}
        )
        out = r.render(CTX, payload([], overall))
        self.assertIn("심각 3건", out)
        self.assertIn("경고 2건", out)

    def test_요약이_없으면_그_블록이_빠진다(self):
        r = MarkdownRenderer()
        out = r.render(CTX, payload([ReportSection(key="k", title="T")]))
        self.assertNotIn("전체 요약", out)

    def test_빈_블록이_남긴_여백은_정리된다(self):
        r = MarkdownRenderer()
        out = r.render(CTX, payload([ReportSection(key="k", title="T")]))
        self.assertNotIn("\n\n\n", out)


class BriefTemplateTest(unittest.TestCase):
    """블록을 정의하지 않으면 그 부분이 통째로 생략된다."""

    def test_판정_블록이_없으면_근거가_생략된다(self):
        r = MarkdownRenderer(template="report_brief.md")
        sec = ReportSection(
            key="k",
            title="T",
            narrative="서술",
            judgements=[
                Judgement(subject="X", severity=Severity.WARNING, reasoning="r",
                          evidence=["e"])
            ],
        )
        out = r.render(CTX, payload([sec]))
        self.assertNotIn("판정 근거", out)
        self.assertIn("서술", out)

    def test_같은_데이터로_다른_양식이_나온다(self):
        sec = ReportSection(key="k", title="T", narrative="서술",
                            metrics=[Metric(name="m", value=1)])
        full = MarkdownRenderer().render(CTX, payload([sec]))
        brief = MarkdownRenderer(template="report_brief.md").render(CTX, payload([sec]))
        self.assertNotEqual(full, brief)
        self.assertIn("| 지표 |", full)
        self.assertNotIn("| 지표 |", brief)


if __name__ == "__main__":
    unittest.main()
