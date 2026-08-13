"""커스텀 리듀서 전용 모듈.

리듀서는 "State가 어떻게 변하는가"를 정의하는 규칙이라 흩어지면
상태 변화를 추적할 수 없게 된다. 그래서 한곳에 모은다.
"""

from __future__ import annotations

from src.domain.models import ReportSection


def merge_sections(
    left: list[ReportSection], right: list[ReportSection]
) -> list[ReportSection]:
    """같은 key가 두 번 오면 나중 것이 이긴다.

    병렬 서브그래프는 서로 다른 key를 내므로 평소엔 단순 append와 같다.
    fork나 재실행으로 같은 key가 다시 들어올 때만 교체가 일어난다.
    """
    merged: dict[str, ReportSection] = {s.key: s for s in left}
    for section in right:
        merged[section.key] = section
    return list(merged.values())
