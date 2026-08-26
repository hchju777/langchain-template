"""질의 분석 노드 — START와 서브그래프 fan-out 사이.

LLM을 **1회** 부른다. 도구 호출도 루프도 없다. 판단에 필요한 재료(질의,
활성 분석 목록)가 프롬프트에 모두 들어 있고 답도 한 번에 나오기 때문이다.

질의가 없으면 LLM을 아예 부르지 않고 전체를 선택한다. 무인 스케줄러가
이 경로로 도므로 비용도 지연도 늘지 않는다.
"""

from __future__ import annotations

import logging

from src.application.graph.state import Dependencies, ReportState
from src.constants import QUERY_PROMPT_MARKER
from src.domain.models import Requirement

logger = logging.getLogger(__name__)

ANALYZE_NODE = "analyze_query"


def _prompt(query: str, active: list[str], titles: dict[str, str]) -> str:
    """등록명과 제목을 함께 싣는다. 'kpi.check'만으로는 무슨 분석인지 모른다."""
    listing = "\n".join(f"- {name}: {titles.get(name, name)}" for name in active)
    return (
        "아래는 이 리포트에서 수행할 수 있는 분석 목록입니다.\n"
        f"{listing}\n\n"
        "사용자 질의에 답하는 데 필요한 분석만 selected에 고르고, 서술에서 "
        "강조할 키워드를 focus에 넣으세요. 목록에 없는 이름은 쓰지 마세요.\n\n"
        f"{QUERY_PROMPT_MARKER}{query}"
    )


def make_analyze_query(deps: Dependencies, active: list[str], titles: dict[str, str]):
    async def analyze_query(state: ReportState) -> dict:
        query = state.ctx.query
        if not query:
            return {"requirement": Requirement(selected=list(active), is_full_scope=True)}

        try:
            req = await deps.llm.plan(ANALYZE_NODE, _prompt(query, active, titles), active)
        except Exception as exc:  # noqa: BLE001 - 여기서 막지 않으면 리포트가 통째로 죽는다
            logger.exception("질의 분석 실패 — 전체 분석으로 진행합니다")
            req = Requirement(query=query, selected=list(active), is_full_scope=True)
            drops = [
                f"{ANALYZE_NODE}: 질의 분석에 실패해 전체 분석으로 진행했습니다 "
                f"({type(exc).__name__})"
            ]
        else:
            drops = []
            req = req.model_copy(update={"query": query})
            if not req.selected:
                # 가드레일이 전부 걷어냈다. 빈 리포트보다 전체 리포트가 낫다.
                req = req.model_copy(
                    update={"selected": list(active), "is_full_scope": True}
                )

        # 성공·실패 어느 쪽이든 어댑터를 비운다. 실패 직전에 기록된 trace를 두고
        # 가면 다음 노드의 drain에 섞여 엉뚱한 노드 것으로 남는다.
        return {
            "requirement": req,
            "traces": deps.llm.drain_traces(),
            "guardrail_drops": deps.llm.drain_guardrail_drops() + drops,
        }

    return analyze_query
