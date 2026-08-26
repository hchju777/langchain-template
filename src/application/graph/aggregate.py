"""취합·렌더·발송 노드.

전체 요약 LLM에게는 **구조화된 judgements와 사전 계산된 집계치**를 준다.
세부 요약 문장을 다시 요약하면 정보 손실이 2단 누적되고, 무엇보다
"critical 몇 건"을 정확히 세지 못한다. 개수·심각도는 코드가 계산해서
넘기고 LLM은 해석과 우선순위만 맡는다.
"""

from __future__ import annotations

import logging
import re

from src.application.graph.state import Dependencies, ReportState
from src.domain.models import DeliveryRecord, OverallSummary, Severity
from src.infrastructure.checkpoint import run_digest

logger = logging.getLogger(__name__)

AGGREGATE_NODE = "aggregate"

#: 대괄호 인용. 소문자·숫자로 시작하는 토큰만 본다. 한글 대괄호 강조와
#: 섞이지 않게 하려는 것이다 (예: "[심각]"은 인용이 아니다).
_CITE = re.compile(r"\[([a-z0-9][\w.\-]*)\]")


def _unknown_citations(text: str, allowed: set[str]) -> list[str]:
    """서술이 인용한 ID 중 실재하지 않는 것.

    aggregate는 narrate()를 쓰므로 judge()의 근거 강제가 걸리지 않는다.
    그래서 사후에 대조한다. LLM 없이 도는 결정론적 검사라 항상 켠다.
    """
    return sorted({m for m in _CITE.findall(text) if m not in allowed})


def make_aggregate(deps: Dependencies):
    async def aggregate(state: ReportState) -> dict:
        judgements = [j for s in state.sections for j in s.judgements]

        # 개수는 코드가 센다. LLM에게 세게 하지 않는다.
        counts = {
            "critical": sum(1 for j in judgements if j.severity is Severity.CRITICAL),
            "warning": sum(1 for j in judgements if j.severity is Severity.WARNING),
            "normal": sum(1 for j in judgements if j.severity is Severity.NORMAL),
            "sections": len(state.sections),
            "degraded": sum(1 for s in state.sections if s.degraded),
        }

        ranked = sorted(judgements, key=lambda j: -int(j.severity))[:5]
        top_issues = [f"[{j.severity.label}] {j.subject} — {j.reasoning}" for j in ranked]

        facts = "\n".join(
            f"- [{s.key}] {s.title}: {s.severity.label}"
            + (f" (판정 {len(s.judgements)}건)" if s.judgements else "")
            + (" (부분 실패)" if s.degraded else "")
            for s in state.sections
        )
        docs = await deps.references.load(state.ctx, state.requirement) \
            if deps.references is not None else []

        prompt = (
            "아래는 각 분석의 판정 결과입니다. 심각 "
            f"{counts['critical']}건, 경고 {counts['warning']}건입니다.\n"
            "운영 담당자가 오늘 무엇부터 봐야 할지 2~3문장으로 정리하세요. "
            "숫자를 새로 만들지 말고 아래 값만 인용하세요.\n" + facts
        )
        if docs:
            # 대괄호 ID를 함께 준다. 서술이 이것을 인용하면 사후 대조가 된다.
            body = "\n\n".join(f"[{d.id}] {d.title}\n{d.content}" for d in docs)
            prompt += (
                "\n\n참고 문서입니다. 판정을 해석할 때 함께 고려하고, 인용할 때는 "
                f"대괄호 안의 id를 그대로 쓰세요.\n{body}"
            )
        if state.requirement and state.requirement.focus:
            prompt += (
                "\n\n사용자가 특히 알고자 하는 것: "
                f"{', '.join(state.requirement.focus)}"
            )

        narrative = await deps.llm.narrate(AGGREGATE_NODE, prompt)

        # 서술 자체는 지우지 않는다. 문장 중간을 잘라내면 읽을 수 없는 글이
        # 되고, 취합 서술은 판정이 아니라 요약이라 폐기 대상이 아니다.
        allowed = {s.key for s in state.sections} | {d.id for d in docs}
        unknown = _unknown_citations(narrative, allowed)
        drops = deps.llm.drain_guardrail_drops()
        if unknown:
            drops.append(
                f"{AGGREGATE_NODE}: 서술이 인용한 {unknown!r}이 실제 자료에 없습니다"
            )

        return {
            "overall": OverallSummary(
                narrative=narrative, counts=counts, top_issues=top_issues
            ),
            "references": docs,
            "traces": deps.llm.drain_traces(),
            "guardrail_drops": drops,
        }

    return aggregate


def make_render(deps: Dependencies):
    async def render(state: ReportState) -> dict:
        content = deps.renderer.render(
            state.ctx,
            {
                "sections": state.sections,
                "overall": state.overall,
                "requirement": state.requirement,
            },
        )
        return {"rendered": content}

    return render


def make_deliver(deps: Dependencies):
    async def deliver(state: ReportState) -> dict:
        ctx = state.ctx
        # 멱등키. 재개하면 State와 함께 복원되므로 중복 발송을 막는다.
        # 질의와 첨부 문서가 결과물을 바꾸므로 둘 다 키에 들어가야 한다 —
        # 빠뜨리면 임시 실행이 정규 리포트 파일을 덮어쓴다.
        key = f"{ctx.gbm}_{ctx.factory}_{ctx.as_of:%Y%m%dT%H%M}"
        digest = run_digest(ctx.query, ctx.attachments)
        if digest:
            key += f"_q{digest}"
        already = {d.channel for d in state.delivered}

        records = []
        for channel in deps.deliveries:
            if channel.channel in already:
                continue
            # 채널 이름을 여기서 비교하지 않는다. 나중에 Slack이 붙어도
            # 같은 규칙이 그대로 적용되어야 한다.
            if ctx.is_ad_hoc and getattr(channel, "broadcast", False):
                logger.info(
                    "channel=%s 임시 실행이라 발송을 건너뜁니다", channel.channel
                )
                continue
            target = await channel.deliver(key, state.rendered or "")
            records.append(
                DeliveryRecord(channel=channel.channel, key=key, target=target)
            )
        return {"delivered": records}

    return deliver
