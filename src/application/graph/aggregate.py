"""취합·렌더·발송 노드.

전체 요약 LLM에게는 **구조화된 judgements와 사전 계산된 집계치**를 준다.
세부 요약 문장을 다시 요약하면 정보 손실이 2단 누적되고, 무엇보다
"critical 몇 건"을 정확히 세지 못한다. 개수·심각도는 코드가 계산해서
넘기고 LLM은 해석과 우선순위만 맡는다.
"""

from __future__ import annotations

from src.application.graph.state import Dependencies, ReportState
from src.domain.models import DeliveryRecord, OverallSummary, Severity

AGGREGATE_NODE = "aggregate"


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
        prompt = (
            "아래는 각 분석의 판정 결과입니다. 심각 "
            f"{counts['critical']}건, 경고 {counts['warning']}건입니다.\n"
            "운영 담당자가 오늘 무엇부터 봐야 할지 2~3문장으로 정리하세요. "
            "숫자를 새로 만들지 말고 아래 값만 인용하세요.\n" + facts
        )
        narrative = await deps.llm.narrate(AGGREGATE_NODE, prompt)

        return {
            "overall": OverallSummary(
                narrative=narrative, counts=counts, top_issues=top_issues
            ),
            "traces": deps.llm.drain_traces(),
            "guardrail_drops": deps.llm.drain_guardrail_drops(),
        }

    return aggregate


def make_render(deps: Dependencies):
    async def render(state: ReportState) -> dict:
        content = deps.renderer.render(
            state.ctx, {"sections": state.sections, "overall": state.overall}
        )
        return {"rendered": content}

    return render


def make_deliver(deps: Dependencies):
    async def deliver(state: ReportState) -> dict:
        ctx = state.ctx
        # 멱등키. 재개하면 State와 함께 복원되므로 중복 발송을 막는다.
        key = f"{ctx.gbm}_{ctx.factory}_{ctx.as_of:%Y%m%dT%H%M}"
        already = {d.channel for d in state.delivered}

        records = []
        for channel in deps.deliveries:
            if channel.channel in already:
                continue
            target = await channel.deliver(key, state.rendered or "")
            records.append(
                DeliveryRecord(channel=channel.channel, key=key, target=target)
            )
        return {"delivered": records}

    return deliver
