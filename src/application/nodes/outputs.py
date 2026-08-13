"""공유 슬롯 부품 — 여러 서브그래프가 config로 갈아끼워 쓴다.

등록명은 모듈 경로 + 함수명에서 유도된다:
    src/application/nodes/outputs.py 의 no_llm  →  "outputs.no_llm"

슬롯 부품은 서브그래프 인스턴스에 바인딩되므로 첫 인자가 self다.
"""

from __future__ import annotations

from src.application.subgraphs.base import SubgraphState
from src.config.registry import register_node
from src.domain.models import ReportSection, Severity


@register_node()
async def no_llm(self, state: SubgraphState) -> dict:
    """LLM 서술 없이 지표와 판정만 낸다.

    특정 GBM/FCT에서 LLM 호출을 줄이고 싶거나, 서술이 필요 없는 분석에
    config로 이 슬롯만 교체한다:

        "nodes": { "output": "outputs.no_llm" }
    """
    severity = max((j.severity for j in state.judgements), default=Severity.NORMAL)
    section = ReportSection(
        key=self.registry_name,
        title=self.title or self.registry_name,
        severity=severity,
        narrative="",
        metrics=state.metrics,
        judgements=state.judgements,
    )
    # 서술은 안 만들지만 관측 기록은 반드시 넘긴다. process에서 LLM judge를
    # 썼다면 프롬프트 원문과 가드레일 경고가 여기 쌓여 있고, 이걸 빠뜨리면
    # 조용히 사라진다.
    return {
        "section": section,
        "traces": self.deps.llm.drain_traces(),
        "guardrail_drops": self.deps.llm.drain_guardrail_drops(),
    }
