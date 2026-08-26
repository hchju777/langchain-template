"""presenter — ReportSection 객체를 사람이 읽는 문자열로 바꾼다.

여기가 presentation인 이유: 이 모듈을 바꾸면 **사용자가 보는 내용이
달라진다.** 반면 그 문자열을 파일로 쓸지 메일로 보낼지는 도착 경로만
바뀌는 일이라 infrastructure/delivery.py가 맡는다.

노드는 ReportRendererPort만 알고 이 모듈도, 템플릿 파일도 모른다.
그래서 "md 대신 HTML 메일"이나 "GBM별 다른 양식"이 config 한 줄이 된다.

템플릿 문법
-----------
    <!-- block: 이름 -->      블록 구분
    ${변수}                    치환 (파이썬 string.Template)

Jinja2를 쓰는 게 원래 계획이지만 이 환경에는 설치돼 있지 않아 표준
라이브러리로 최소 엔진을 구현했다. 포트 뒤에 있으므로 Jinja2 어댑터를
추가하고 Dependencies에서 바꿔 끼우면 노드는 그대로다.
"""

from __future__ import annotations

import re
from pathlib import Path
from string import Template

from src.constants import DEFAULT_REPORT_TEMPLATE, TEMPLATE_ROOT
from src.domain.models import BaseContext, Metric, ReportSection, Severity

# 구분자 "줄"만 잡는다. 본문의 빈 줄은 템플릿 저자의 의도이므로 건드리지 않는다.
BLOCK_PATTERN = re.compile(r"^<!--[ \t]*block:[ \t]*(?P<name>[\w.-]+)[ \t]*-->[ \t]*\n", re.M)
COMMENT_PATTERN = re.compile(r"<!--(?![ \t]*block:).*?-->", re.S)
BLANK_RUN = re.compile(r"\n{3,}")

SEVERITY_MARK = {
    Severity.NORMAL: "🟢",
    Severity.WARNING: "🟡",
    Severity.CRITICAL: "🔴",
}


class TemplateError(RuntimeError):
    """템플릿 파일이 없거나 필요한 블록이 빠졌을 때."""


def load_blocks(path: Path) -> dict[str, str]:
    """템플릿 파일을 블록 사전으로 파싱한다."""
    if not path.exists():
        raise TemplateError(f"템플릿 파일이 없습니다: {path}")

    raw = COMMENT_PATTERN.sub("", path.read_text(encoding="utf-8"))
    parts = BLOCK_PATTERN.split(raw)
    # split 결과: [머리말, 이름1, 본문1, 이름2, 본문2, ...]
    blocks = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    if "document" not in blocks:
        raise TemplateError(f"{path}에 'document' 블록이 없습니다")
    return blocks


def _fill(block: str, values: dict) -> str:
    """빠진 변수는 빈 문자열로 둔다. 템플릿이 덜 채워도 죽지 않는다."""
    return Template(block).safe_substitute(
        {k: ("" if v is None else v) for k, v in values.items()}
    )


def _render_metrics(metrics: list[Metric]) -> str:
    """숫자는 여기서 State의 값을 직접 찍는다. LLM이 다시 쓰지 않는다."""
    if not metrics:
        return ""
    head = "| 지표 | 값 | 비고 |\n| --- | ---: | --- |"
    rows = [
        f"| {m.name} | {m.value}{(' ' + m.unit) if m.unit else ''} | {m.note} |"
        for m in metrics
    ]
    return "\n".join([head, *rows])


class MarkdownRenderer:
    """템플릿 파일 기반 md 렌더러."""

    name = "markdown"

    def __init__(
        self, template: str = DEFAULT_REPORT_TEMPLATE, root: Path | None = None
    ) -> None:
        self.path = (root or TEMPLATE_ROOT) / template
        self.blocks = load_blocks(self.path)  # 부팅 시 1회. 없으면 여기서 터진다

    def render(self, ctx: BaseContext, payload: dict) -> str:
        sections: list[ReportSection] = payload["sections"]
        overall = payload.get("overall")

        document = _fill(
            self.blocks["document"],
            {
                "gbm": ctx.gbm.upper(),
                "factory": ctx.factory.upper(),
                "as_of": ctx.as_of.isoformat(),
                "section_count": len(sections),
                "scope": self._render_scope(payload.get("requirement")),
                "overall": self._render_overall(overall),
                "sections": self._render_sections(sections),
            },
        )
        # 비어버린 블록(요약 없음, 지표 없음)이 남긴 여백을 정리한다.
        return BLANK_RUN.sub("\n\n", document).strip() + "\n"

    @staticmethod
    def _render_scope(requirement) -> str:
        """질의로 범위를 좁힌 리포트임을 밝힌다.

        전체 실행이면 아무것도 쓰지 않는다. 스케줄러 리포트의 모양이
        바뀌지 않아야 하기 때문이다.
        """
        if requirement is None or requirement.is_full_scope:
            return ""
        picked = ", ".join(f"`{name}`" for name in requirement.selected)
        return f"\n- 질의: {requirement.query}\n- 선택된 분석: {picked}"

    # ------------------------------------------------------------------
    def _render_overall(self, overall) -> str:
        if overall is None or "overall" not in self.blocks:
            return ""
        counts = overall.counts
        top = "\n".join(
            f"{i}. {issue}" for i, issue in enumerate(overall.top_issues, 1)
        )
        return _fill(
            self.blocks["overall"],
            {
                "critical": counts.get("critical", 0),
                "warning": counts.get("warning", 0),
                "normal": counts.get("normal", 0),
                "degraded": counts.get("degraded", 0),
                "narrative": overall.narrative,
                "top_issues": f"\n**우선 확인**\n{top}" if top else "",
            },
        )

    def _render_sections(self, sections: list[ReportSection]) -> str:
        block = self.blocks.get("section")
        if not block:
            return ""
        # 심각한 것부터. 같은 심각도면 이름순으로 안정 정렬.
        ordered = sorted(sections, key=lambda s: (-int(s.severity), s.key))
        # 블록이 자기 앞뒤 여백을 들고 있으므로 그냥 잇는다.
        return "".join(self._render_section(block, s) for s in ordered)

    def _render_section(self, block: str, s: ReportSection) -> str:
        return _fill(
            block,
            {
                "key": s.key,
                "title": s.title,
                "mark": SEVERITY_MARK[s.severity],
                "severity": s.severity.label,
                "degraded_mark": " _(부분 실패)_" if s.degraded else "",
                "narrative": s.narrative,
                # 한 줄짜리 템플릿용. 줄바꿈을 없앤 서술.
                "narrative_inline": " ".join(s.narrative.split()) or "특이사항 없음",
                "metrics": _render_metrics(s.metrics),
                "judgements": self._render_judgements(s),
            },
        )

    def _render_judgements(self, s: ReportSection) -> str:
        wrapper = self.blocks.get("judgements")
        item_block = self.blocks.get("judgement_item")
        if not s.judgements or not wrapper or not item_block:
            return ""
        items = "".join(
            _fill(
                item_block,
                {
                    "subject": j.subject,
                    "severity": j.severity.label,
                    "confidence": f"{j.confidence:.0%}",
                    "reasoning": j.reasoning,
                    "evidence": ", ".join(j.evidence) or "없음",
                },
            )
            for j in s.judgements
        )
        return _fill(wrapper, {"items": items})
