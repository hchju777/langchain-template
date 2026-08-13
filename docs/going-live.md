# 실제 연결로 전환

스텁을 실제 Redis / MongoDB / Kafka / REST / LLM으로 바꾸는 절차입니다.

> ### ⚠ 이 문서는 검증되지 않았습니다
>
> 다른 문서와 달리 이 내용은 **실제로 돌려본 것이 아닙니다.** 이 저장소에는 붙일 서버가 없어서, 각 라이브러리의 API를 근거로 쓴 안내입니다.
>
> 실제로 붙이시면서 **다른 점이 나오면 이 문서를 고쳐주세요.** 특히 쿼리 형태와 반환값 구조는 여러분의 스키마에 달려 있습니다.

---

## 전체 그림

바꿔야 할 곳은 **네 군데**뿐입니다.

| 무엇 | 어디 | 바꾸는 것 |
|---|---|---|
| 저장소 조회 | `src/infrastructure/stores.py` | `fake_data` 호출 → 실제 쿼리 |
| 접속 정보 | `.env` | 주소·계정·비밀번호 |
| LLM | config `llm.adapter` | `"fake"` → `"chat_model"` |
| 체크포인터 | config `checkpoint.backend` | `"memory"` → `"mongodb"` |

**서브그래프 코드는 하나도 안 바뀝니다.** 포트 뒤에 있어서요.

---

## 1. 접속 정보부터

```bash
cp .env.example .env
```

```bash
REDIS_URL=redis://redis.internal:6379/0
REDIS_PWD=...

MONGODB_URI=mongodb://mongo.internal:27017
MONGODB_PWD=...
MONGODB_DB=mes
CHECKPOINT_DB=langgraph

KAFKA_BOOTSTRAP_SERVERS=kafka1.internal:9092,kafka2.internal:9092
REST_BASE_URL=https://mes-api.internal
REST_API_TOKEN=...

LLM_BASE_URL=http://llm-gateway.internal/v1
LLM_API_KEY=...
```

`.env`는 `.gitignore`에 있습니다. 비밀값을 `config/*.json`에 넣지 마세요 — 그쪽은 커밋됩니다.

---

## 2. 어댑터에 커넥션 붙이기

지금 어댑터는 커넥션을 만들지 않습니다. `BaseAdapter`를 상속하면 **타임아웃·재시도·`_to_domain` 규약을 그대로 물려받으니** 조회 본문만 채우면 됩니다.

### 커넥션 수명

프로세스 시작 시 **한 번** 만들어 `Dependencies`에 담습니다. 노드가 매번 열면 상주 스케줄러에서 커넥션이 샙니다.

`build_dependencies`([builder.py](../src/application/graph/builder.py))에 이미 자리가 있습니다.

```python
adapters = {
    RedisAdapter.name: RedisAdapter(
        url=env.redis_url, password=env.redis_password, timeout=timeout
    ),
    MongoAdapter.name: MongoAdapter(
        uri=env.mongodb_uri, password=env.mongodb_password,
        database=env.mongodb_database, timeout=timeout
    ),
    ...
}
```

그리고 `close()`가 `Dependencies.adapters`를 순회하므로, 어댑터마다 `close()`만 구현하면 정리됩니다.

### Redis

```python
class RedisAdapter(_PingMixin, BaseAdapter):
    name = "redis"
    supported_kinds = ("production", "line_info", "equipment_status", "material_stock")

    def __init__(self, url: str, password: str = "", **kw):
        super().__init__(**kw)
        import redis.asyncio as aioredis
        self._client = aioredis.from_url(url, password=password or None,
                                         decode_responses=True)

    async def close(self):
        await self._client.aclose()

    async def fetch(self, ctx, spec):
        async def _do():
            pattern = KEY_PATTERNS[spec.kind]          # 예: "equip:*"
            keys = [k async for k in self._client.scan_iter(match=pattern)]
            values = await self._client.mget(keys) if keys else []
            return [self._parse(k, v) for k, v in zip(keys, values) if v]

        return [self._to_domain(r) for r in await self._call(_do)]
```

> `KEYS` 대신 **`SCAN`**을 쓰세요. `KEYS`는 서버를 블로킹합니다.

`_parse`가 Redis 값(보통 JSON 문자열)을 `{id, metadata, record}` dict로 바꾸는 자리입니다. **이 변환은 어댑터 안에서 끝나야** 서브그래프가 키 구조를 모릅니다.

### MongoDB — `motor`가 아니라 PyMongo Async

motor는 **2026-05-14에 deprecated** 되었습니다. 새 코드는 `AsyncMongoClient`를 씁니다.

```python
class MongoAdapter(_PingMixin, BaseAdapter):
    name = "mongodb"
    supported_kinds = ("alarms",)

    def __init__(self, uri: str, database: str, password: str = "", **kw):
        super().__init__(**kw)
        from pymongo import AsyncMongoClient
        self._client = AsyncMongoClient(uri, password=password or None,
                                        serverSelectionTimeoutMS=3000)
        self._db = self._client[database]

    async def close(self):
        await self._client.close()

    async def fetch(self, ctx, spec):
        async def _do():
            cursor = self._db.alarms.find(
                {"raised_at": {"$gte": ctx.start_dt, "$lt": ctx.end_dt}}
            )
            return [self._parse(doc) async for doc in cursor]

        return [self._to_domain(r) for r in await self._call(_do)]
```

**주의할 것들**

- 구간 조회 필드에 **인덱스**가 있어야 합니다 (`raised_at`)
- Mongo는 `Decimal128`을 돌려줄 수 있습니다. `float()`로 변환하지 않으면 나중에 산술에서 터집니다
- `ObjectId`는 그대로 두면 직렬화가 안 됩니다. `str()`로 바꾸세요

### Kafka — 메시지를 소비하지 않습니다

lag은 오프셋 메타데이터만으로 계산됩니다.

```python
class KafkaAdminAdapter(_PingMixin, BaseAdapter):
    name = "kafka"
    supported_kinds = ("consumer_lag",)

    def __init__(self, bootstrap_servers: str, **kw):
        super().__init__(**kw)
        from aiokafka.admin import AIOKafkaAdminClient
        self._admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap_servers)

    async def fetch(self, ctx, spec):
        async def _do():
            groups = spec.filters.get("groups") or await self._all_groups()
            rows = []
            for group in groups:
                committed = await self._admin.list_consumer_group_offsets(group)
                for tp, meta in committed.items():
                    end = await self._end_offset(tp)
                    rows.append({
                        "id": f"lag:{group}:{tp.topic}:{tp.partition}",
                        "metadata": {"group": group, "topic": tp.topic,
                                     "partition": tp.partition},
                        "record": {"end_offset": end,
                                   "committed_offset": meta.offset,
                                   "lag": end - meta.offset},
                    })
            return rows

        return [self._to_domain(r) for r in await self._call(_do)]
```

`end_offset`은 `AIOKafkaConsumer.end_offsets()`로 가져옵니다 — 컨슈머를 만들되 **구독하지 않고** 메타데이터만 봅니다.

> `AIOKafkaAdminClient`의 정확한 메서드 이름과 반환 타입은 aiokafka 버전에 따라 다를 수 있습니다. 여기가 이 문서에서 **가장 불확실한 부분**입니다.

### REST

```python
class RestAdapter(_PingMixin, BaseAdapter):
    name = "rest"
    supported_kinds = ("kpi",)

    def __init__(self, base_url: str, token: str = "", **kw):
        super().__init__(**kw)
        import httpx
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=self.timeout,
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )

    async def close(self):
        await self._client.aclose()

    async def fetch(self, ctx, spec):
        async def _do():
            r = await self._client.get("/kpi", params={"as_of": ctx.as_of.isoformat()})
            r.raise_for_status()
            return [self._parse(item) for item in r.json()["items"]]

        return [self._to_domain(r) for r in await self._call(_do)]
```

`AsyncClient`를 **재사용**하세요. 요청마다 만들면 커넥션 풀 이점이 사라집니다.

---

## 3. 헬스체크

`_PingMixin`이 지금은 가짜를 돌려줍니다. 어댑터별로 실제 명령을 넣으세요.

| 어댑터 | 명령 |
|---|---|
| redis | `await self._client.ping()` |
| mongodb | `await self._client.admin.command("ping")` |
| kafka | `await self._admin.describe_cluster()` |
| rest | `(await self._client.get("/health")).raise_for_status()` |

`health.connectivity` 서브그래프가 이걸 호출해 리포트 첫 섹션을 만듭니다.

---

## 4. LLM

**코드를 고칠 필요가 없습니다.** config 한 줄입니다.

```json
{ "llm": { "adapter": "chat_model", "provider": "openai_compatible",
           "model": "gpt-4o-mini", "temperature": 0.0 } }
```

사내 게이트웨이가 OpenAI 호환이면 `.env`의 `LLM_BASE_URL`만 맞추면 됩니다.

**처음에는 한 서브그래프만** 실제 LLM에 붙여 프롬프트를 다듬는 걸 권합니다.

```json
{
  "llm": { "adapter": "fake" },
  "subgraphs": { "kpi.check": { "llm": { "adapter": "chat_model", "model": "gpt-4o-mini" } } }
}
```

### 실제 LLM에서 확인할 것

- **structured output**이 되는 모델인가 — `judge()`가 `with_structured_output`을 씁니다. 지원하지 않으면 판정 경로가 실패합니다
- **가드레일 로그**를 보세요. 근거 폐기가 잦다면 프롬프트에 id를 충분히 싣지 않은 것입니다
- 첫 실행에 `--save-traces`를 걸어두면 프롬프트를 실제로 어떻게 보냈는지 볼 수 있습니다

---

## 5. 체크포인터

```json
{ "checkpoint": { "backend": "mongodb" } }
```

`.env`의 `MONGODB_URI`와 `CHECKPOINT_DB`를 씁니다. 데이터용 DB와 **분리하는 걸** 권합니다 — 체크포인트는 쓰기가 잦고 수명 관리가 다릅니다.

연결이 안 되면 부팅에서 멈춥니다.

```
✗ MongoDB에 연결할 수 없습니다: mongodb://...
  ServerSelectionTimeoutError: ...
MONGODB_URI를 확인하거나, 서버 없이 개발 중이라면 checkpoint.backend를 'memory'로 두세요.
```

체크포인트는 계속 쌓입니다. **TTL 인덱스**나 주기적 정리를 계획하세요.

---

## 6. 발송

`MailDelivery`가 지금은 문자열만 돌려줍니다. `aiosmtplib`로 채우세요.

```python
async def deliver(self, key: str, content: str) -> str:
    from email.message import EmailMessage
    import aiosmtplib

    msg = EmailMessage()
    msg["Subject"] = f"{self.subject_prefix} {key}"
    msg["From"] = self.sender
    msg["To"] = ", ".join(self.recipients)
    msg.set_content(content)

    await aiosmtplib.send(msg, hostname=self.host, port=self.port,
                          username=self.username or None,
                          password=self.password or None)
    return f"mail://{','.join(self.recipients)}"
```

md 원문을 그대로 보내면 메일 클라이언트에서 읽기 어렵습니다. HTML로 보내려면 **렌더러를 하나 더 만들어** `ReportRendererPort`를 구현하는 쪽이 맞습니다 — 발송 코드에 마크업을 넣지 마세요.

---

## 7. 전환 순서

한 번에 다 바꾸지 마세요. 무엇이 깨졌는지 알 수 없게 됩니다.

1. **`health.connectivity`만** 실제 연결로 — 접속 정보가 맞는지부터 확인
2. **저장소 하나씩** — 한 어댑터 바꾸고 그 서브그래프만 돌려봅니다
3. **LLM** — 서브그래프 하나만 먼저
4. **체크포인터**
5. **스케줄러 상주 모드**

각 단계에서 `--save-traces`로 실제 프롬프트를, `--show-checkpoints`로 실행 흐름을 확인할 수 있습니다.

`fake_data.py`는 마지막에 지우세요. 실제 연결이 불안정할 때 되돌아갈 곳이 있는 편이 낫습니다.

---

## 8. 스텁일 때와 달라지는 것

| | 스텁 | 실제 |
|---|---|---|
| 재실행 결과 | 항상 동일 (`as_of` 시드) | **데이터가 바뀌면 달라짐** |
| 타임아웃 | 발생 안 함 | 자주 발생 → `handle_error`로 |
| 데이터 모양 | 항상 예상대로 | null·타입 불일치·필드 누락 |
| LLM | 규칙 기반 | 비결정적, 비용 발생 |

**어댑터 테스트가 없다는 게 여기서 드러납니다.** 변환 오류(`Decimal128`, null, 필드명)가 운영에서 처음 발견될 수 있습니다. 실제 응답을 한 번 떠서 `tests/fixtures/`에 저장하고 `_to_domain`을 검증하는 테스트를 추가하면 Docker 없이도 상당 부분 막힙니다 — [설계 문서 §11](superpowers/specs/2026-08-13-langgraph-report-template-design.md)에 그 판단의 배경이 있습니다.
