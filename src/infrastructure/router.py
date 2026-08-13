"""kind → 어댑터 라우팅.

서브그래프가 "material_stock을 달라"고 하면, 그것이 Redis에서 오는지
REST에서 오는지는 **config가** 정합니다.

    // config
    { "ports": { "material_stock": "redis", "alarms": "mongodb" } }

    # 서브그래프 — 저장소를 모른다
    await self.deps.data.fetch(ctx, FetchSpec(kind="material_stock"))

이 라우터가 없으면 서브그래프가 `self.deps.redis`를 직접 부르게 되고,
그 순간 "이 분석은 Redis에서 읽는다"가 코드에 박혀 저장소를 옮길 때마다
분석 로직을 고쳐야 합니다.
"""

from __future__ import annotations

from typing import Any

from src.domain.models import BaseContext, FetchSpec, Record


class UnroutedKindError(KeyError):
    """config의 ports에 매핑이 없는 kind를 요청했을 때."""

    def __init__(self, kind: str, known: list[str]) -> None:
        self.kind = kind
        available = ", ".join(sorted(known)) or "(비어 있음)"
        super().__init__(
            f"'{kind}'을(를) 어디서 가져올지 config의 ports에 없습니다. "
            f"매핑된 kind: {available}"
        )


class DataRouter:
    """DataPort 구현. 라우팅만 하고 조회는 어댑터에 넘긴다."""

    def __init__(self, routes: dict[str, Any]) -> None:
        #: kind → 어댑터 인스턴스
        self._routes = dict(routes)

    def adapter_for(self, kind: str) -> Any:
        adapter = self._routes.get(kind)
        if adapter is None:
            raise UnroutedKindError(kind, list(self._routes))
        return adapter

    def routed_kinds(self) -> set[str]:
        return set(self._routes)

    async def fetch(self, ctx: BaseContext, spec: FetchSpec) -> list[Record]:
        return await self.adapter_for(spec.kind).fetch(ctx, spec)
