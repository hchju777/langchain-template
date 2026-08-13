"""Redis / MongoDB / Kafka Admin / REST 어댑터.

전부 스텁이다. 실제 연결부는 주석으로 위치만 표시하고, 지금은
fake_data의 dict 조회로 대신한다. 각 어댑터가 원본을 Record로 바꾸는
_to_domain 호출 위치는 실제 구현과 동일하다.
"""

from __future__ import annotations

from src.infrastructure import fake_data
from src.infrastructure.base import BaseAdapter
from src.domain.models import (
    BaseContext,
    FetchSpec,
    HistoricalContext,
    Record,
    SnapshotContext,
)


class _PingMixin:
    """헬스체크 공통. 실제 명령만 어댑터마다 다르다."""

    name: str

    async def ping(self, ctx: BaseContext) -> Record:
        async def _do():
            # 실제 구현은 어댑터마다 다르다:
            #   redis   : await self._client.ping()
            #   mongodb : await self._client.admin.command("ping")
            #   kafka   : await self._admin.describe_cluster()
            #   rest    : (await self._client.get("/health")).raise_for_status()
            return fake_data.health_snapshot(ctx.as_of, self.name)

        payload = await self._call(_do)  # type: ignore[attr-defined]
        return Record(
            id=f"health:{self.name}",
            metadata={"source": self.name},
            record=payload,
        )


class RedisAdapter(_PingMixin, BaseAdapter):
    """생산정보 / 라인 정보 / 장비 상태 스냅샷."""

    name = "redis"

    # 실제 구현:
    # def __init__(self, dsn, password, **kw):
    #     super().__init__(**kw)
    #     self._client = redis.asyncio.from_url(dsn, password=password)

    async def fetch(self, ctx: SnapshotContext, spec: FetchSpec) -> list[Record]:
        async def _do():
            # 실제: keys = await self._client.keys(pattern)
            #       vals = await self._client.mget(keys)
            table = {
                "production": fake_data.redis_production,
                "line_info": fake_data.redis_line_info,
                "equipment_status": fake_data.redis_equipment_status,
                "material_stock": fake_data.redis_material_stock,
            }
            if spec.kind not in table:
                raise KeyError(f"redis: 알 수 없는 kind '{spec.kind}'")
            return table[spec.kind](ctx.as_of)

        return [self._to_domain(r) for r in await self._call(_do)]


class MongoAdapter(_PingMixin, BaseAdapter):
    """알람 이력 등 구간 조회의 주력. historical은 Kafka가 아니라 여기서."""

    name = "mongodb"

    # 실제 구현:
    # self._client = AsyncIOMotorClient(dsn, password=...)
    # self._coll = self._client[db][collection]

    async def fetch(self, ctx: HistoricalContext, spec: FetchSpec) -> list[Record]:
        async def _do():
            # 실제:
            #   cursor = self._coll.find({"raised_at": {"$gte": ctx.start_dt,
            #                                           "$lt":  ctx.end_dt}})
            #   return [doc async for doc in cursor]
            if spec.kind != "alarms":
                raise KeyError(f"mongodb: 알 수 없는 kind '{spec.kind}'")
            return fake_data.mongo_alarms(ctx.start_dt, ctx.end_dt)

        return [self._to_domain(r) for r in await self._call(_do)]


class KafkaAdminAdapter(_PingMixin, BaseAdapter):
    """오프셋 메타데이터만 읽는다. 메시지는 한 건도 소비하지 않는다.

    lag = end_offset - committed_offset 이므로 AdminClient/Consumer의
    메타데이터 API만으로 계산된다. 가볍고 배치에 적합하다.
    """

    name = "kafka"

    # 실제 구현:
    # self._admin = AIOKafkaAdminClient(bootstrap_servers=..., ...)

    async def fetch(self, ctx: SnapshotContext, spec: FetchSpec) -> list[Record]:
        async def _do():
            # 실제:
            #   committed = await self._admin.list_consumer_group_offsets(group)
            #   end       = await consumer.end_offsets(partitions)
            #   lag       = end - committed
            if spec.kind != "consumer_lag":
                raise KeyError(f"kafka: 알 수 없는 kind '{spec.kind}'")
            rows = fake_data.kafka_consumer_lag(ctx.as_of)
            groups = spec.filters.get("groups")
            if groups:
                rows = [r for r in rows if r["metadata"]["group"] in groups]
            return rows

        return [self._to_domain(r) for r in await self._call(_do)]


class RestAdapter(_PingMixin, BaseAdapter):
    """KPI 조회. 타임아웃·재시도가 특히 중요한 어댑터."""

    name = "rest"

    # 실제 구현:
    # self._client = httpx.AsyncClient(base_url=..., timeout=self.timeout,
    #                                  headers={"Authorization": f"Bearer {token}"})

    async def fetch(self, ctx: SnapshotContext, spec: FetchSpec) -> list[Record]:
        async def _do():
            # 실제: r = await self._client.get("/kpi", params={...}); return r.json()
            if spec.kind != "kpi":
                raise KeyError(f"rest: 알 수 없는 kind '{spec.kind}'")
            return fake_data.rest_kpis(ctx.as_of)

        return [self._to_domain(r) for r in await self._call(_do)]
