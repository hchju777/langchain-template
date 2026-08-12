# LangGraph 기반 상태 분석·리포팅 템플릿 — 설계

작성일: 2026-08-13
출처: `memo.md` 초안을 브레인스토밍으로 구체화

## 1. 목적과 범위

### 목적

여러 비동기 데이터 소스에서 운영 지표를 수집해 분석하고, 그 결과를 md 리포트와 메일로 내보내는 **재사용 가능한 템플릿**을 만든다. 특정 서비스 하나를 위한 앱이 아니라, GBM/FCT(사업부/공장)마다 config만 바꿔 찍어내는 골격이 목표다.

분석 대상 도메인은 고정하지 않는다. 템플릿은 "어떤 지표든 받아서 분석·리포팅하는 뼈대"를 제공하고, 무엇을 분석할지는 각 프로젝트가 정의한다.

### 범위에 포함

- config 3단 병합과 부팅 시 검증
- 서브그래프 자동 등록·조립 메커니즘
- 표준 서브그래프 4슬롯 규약과 부분 실패 격리
- 수집 계층의 기반(BaseAdapter 4종, 포트 규약, 바인딩) + 레퍼런스 포트 1개
- LLM 호출 단일 경로, 근거 강제 스키마, 환각 가드레일, replay
- 리포트 렌더링과 발송(멱등성 포함)
- CLI 진입점과 상주 스케줄러

### 범위에서 제외

- 도메인별 분석 로직 (각 프로젝트가 작성)
- 도메인 포트 정의 (각 프로젝트가 작성)
- Kafka 메시지 소비·적재 (별도 인제스터의 책임)
- 어댑터 통합 테스트 (§10, §11 참조)

## 2. 아키텍처

의존 방향은 한 방향이다. `domain`은 바깥을 모른다.

```
 Driving(primary)                          Driven(secondary)
 ┌─────────────┐                          ┌──────────────────┐
 │ CLI         │──→ ┌─────────────┐ ──→   │ Mongo/Redis/REST │
 │ Scheduler   │    │ application │       │ Kafka Admin, LLM │
 └─────────────┘    │   ↓ domain  │       │ SMTP, Checkpoint │
                    └─────────────┘  ──→  │ Renderer         │
                                          └──────────────────┘
```

Clean의 presentation(Interface Adapters)은 별도 폴더가 아니라 양쪽 어댑터에 흡수된다. driving 쪽의 "인자 → BaseContext 변환"이 controller, driven 쪽의 renderer가 presenter다.

### 디렉터리

```
src/
  domain/
    models/          MetricRecord, Judgement, ReportSection, Severity, Context 계열
    ports/           Protocol 정의 (MetricPort 등)
    reducers.py      커스텀 리듀서 전용 — 한 곳에 모은다
  application/
    graph/           부모 그래프 조립(Composition Root), 취합 노드
    subgraphs/       ← 자동 스캔 대상. 경로가 곧 등록명
      base.py          BaseSubgraph (4슬롯)
      analysis/latency.py      → "analysis.latency"
    nodes/           공유 슬롯 부품 (validators, renderers, errors)
    decorators/      timing, error_handling, cache/replay
  adapters/
    driving/         cli, scheduler
    driven/          mongo, redis, kafka_admin, rest, llm, mail, checkpoint, renderers
  config/            ← 설정을 "읽는 코드"
    loader.py        3단 deep merge
    env.py           EnvConfig (.env 비밀값)
    registry.py      패키지 자동 스캔 + @register + 부팅 검증
  templates/         report.md.j2
  constants.py       매직값 금지 — 상수는 여기 또는 각 모듈 상단

config/              ← 설정 "데이터" (저장소 최상위, src/ 바깥)
  gbm/{gbm}.json
  factories/{factory}/common.json
  factories/{factory}/{gbm}.json
```

`src/config/`(코드)와 최상위 `config/`(JSON 데이터)는 이름이 같지만 다른 것이다. 전자는 후자를 읽는 로직이다.

폴더 이름은 Hexagonal 어휘(`adapters/`, `ports/`)를 따른다. `driving`/`driven`은 Cockburn의 원어에 가장 가깝다. `infrastructure/`가 아닌 `adapters/`를 택한 이유는 renderer와 CLI가 자연스럽게 들어가기 때문이다 — 이들을 "인프라"라고 부르면 갈 곳이 애매해진다.

## 3. Config와 등록 체계

### 3단 병합

뒤가 앞을 덮어쓴다. 모든 파일은 JSON.

```
config/gbm/{gbm}.json
  → config/factories/{factory}/common.json
    → config/factories/{factory}/{gbm}.json
```

### 등록: 경로 유도 이름

`application/subgraphs/` 아래 패키지를 부팅 시 재귀 스캔해 import하면, 데코레이터가 레지스트리에 자기를 등록한다. 이름을 주지 않으면 **모듈의 상대 경로가 그대로 등록명**이 된다.

```
application/subgraphs/analysis/latency.py   →  "analysis.latency"
application/subgraphs/kafka/lag.py          →  "kafka.lag"
```

```json
{
  "subgraphs": {
    "analysis.latency": { "enabled": true, "threshold_ms": 500, "window": "24h" },
    "kafka.lag":        { "enabled": true }
  }
}
```

config의 이름은 경로 문자열이 아니라 **레지스트리 키**다. 그래서 파일을 옮기거나 이름을 바꿀 때 `@register("analysis.latency")`로 이름을 고정하면 config를 건드리지 않아도 된다. 평소에는 아무것도 쓰지 않고, 리팩토링 내성이 필요할 때만 한 줄 추가한다.

### 층위: 서브그래프가 1급

서브그래프가 config의 1급 시민이고, 노드 슬롯은 필요할 때만 override한다.

```json
{ "subgraphs": { "analysis.latency": {
    "enabled": true, "threshold_ms": 500,
    "nodes": { "validate": "validators.strict" }
} } }
```

새 분석 추가는 **파일 1개 + config 1줄**이 기본이다. 슬롯 override는 GBM/FCT별로 특정 노드만 달라야 할 때 쓴다.

### GBM/FCT별 분기는 config로 일원화

코드 레벨 특수화(`register(gbm="mx", fct="GUMI", ...)`)는 두지 않는다. 3단 merge가 이미 GBM/FCT 해석기이므로 두 번째 해석기를 만들면 "지금 실제로 도는 구현이 무엇인가"를 알기 위해 두 군데를 대조해야 하고, 충돌 시 우선순위 규칙을 또 정해야 한다.

**상속은 코드에서, 선택은 config에서.**

```python
# subgraphs/analysis/latency_gumi.py   → "analysis.latency_gumi"
class GumiLatency(Latency):
    async def process(self, state): ...
```
```json
// config/factories/gumi/mx.json
{ "subgraphs": { "analysis.latency": { "nodes": { "process": "analysis.latency_gumi" } } } }
```

### 부팅 시 3중 검증

하나라도 실패하면 프로세스가 뜨지 않는다. 에러는 모아서 한 번에 던진다.

1. **이름 대조** — config에 적힌 서브그래프가 레지스트리에 있는가 / 레지스트리에 있는데 어느 config에도 없는 고아가 있는가
2. **스키마 검증** — 각 서브그래프 config가 자기 `BaseModel`을 만족하는가
3. **참조 무결성** — `nodes` 슬롯 override가 가리키는 이름이 실재하는가, 템플릿 파일이 존재하는가

### 진단 CLI

```bash
python -m app config show --gbm mx --factory gumi
```

병합된 최종 config와 **각 값이 어느 파일에서 왔는지**를 출력한다. 3단 merge에서 "이 값이 왜 이래?"는 반드시 나오는 질문이고, 이게 없으면 파일 셋을 눈으로 대조하게 된다.

### 비밀값

JSON은 git에 커밋되므로 비밀값이 들어가지 않는다. 비밀번호·토큰은 `.env` → `EnvConfig`(pydantic-settings)로만 온다.

### config 주입: 조립 시점에만

config 객체(dict)를 노드에 넘기지 않는다. config **값**이 비즈니스 판단을 좌우하는 것은 당연하지만, 노드가 dict를 들고 다니며 `config.get(...)`을 호출하면 (a) 시그니처가 아무 정보를 주지 않고, (b) 테스트에 merge된 dict 전체를 재현해야 하고, (c) 키 오타가 해당 코드 경로 실행 시점에야 터진다.

config 섹션은 Pydantic 모델로 파싱해 팩토리에 넘긴다.

```python
class LatencyConfig(BaseModel):
    threshold_ms: int
    percentile: int = 95
    window: timedelta

def build(config: LatencyConfig, deps: Dependencies):     # 팩토리 — config를 보는 유일한 곳
    threshold = config.threshold_ms
    port = deps.metric_port

    async def process(state: LatencyInput) -> ProcessResult:
        records = await port.fetch(state, MetricSpec(percentile=config.percentile))
        ...
    return process
```

```python
# application/graph/builder.py — Composition Root
for name, sub_cfg in cfg.subgraphs.items():
    if not sub_cfg.enabled:
        continue
    node = registry.get(name).build(sub_cfg, deps)
    node = with_cache(node, ttl=sub_cfg.cache_ttl)
    node = with_error_handling(with_timing(node))
    builder.add_node(name, node)
```

이 구조가 memo.md의 세 요구를 동시에 만족한다 — 팩토리 패턴 노드, 데코레이터 조합, 노드별 캐시 설정. 그리고 노드 테스트가 `process(state)` 호출 한 줄이 된다.

## 4. 그래프 규약

### 실행 컨텍스트

시간 성격에 따라 두 부모 컨텍스트를 두고, 각 서브그래프가 둘 중 하나를 상속해 자기 input schema를 정의한다.

```python
class BaseContext(BaseModel):
    as_of: datetime            # 기준 시각 — 항상 존재
    gbm: str
    factory: str

class SnapshotContext(BaseContext): ...
class HistoricalContext(BaseContext):
    start_dt: datetime
    end_dt: datetime
```

```python
class KafkaLagInput(SnapshotContext):
    consumer_groups: list[str]

class LatencyInput(HistoricalContext):
    percentile: int = 95
```

**`as_of`는 반드시 밖에서 주입한다.** 노드 안에서 `datetime.now()`를 부르면 Durable Execution(재개), Time Travel(과거 재실행), 멱등성이 모두 깨진다. 재개 시각이 달라져 앞뒤 노드가 서로 다른 시점을 보게 되기 때문이다.

config에는 상대 시간(`"window": "24h"`)으로 쓰고, `validate_input`이 `as_of`로부터 절대 시각 쌍을 계산한다. config는 읽기 쉽고 실행은 절대 시각으로 고정된다.

### State

```python
class ReportState(BaseModel):
    ctx: BaseContext                                            # 불변
    sections:  Annotated[list[ReportSection],  merge_sections]
    errors:    Annotated[list[SubgraphError],  operator.add]
    traces:    Annotated[list[LLMTrace],       operator.add]
    overall:   OverallSummary | None = None
    delivered: Annotated[list[DeliveryRecord], operator.add]
```

**커스텀 리듀서는 `domain/reducers.py` 한 곳에 모은다.** 리듀서는 "State가 어떻게 변하는가"를 정의하는 규칙이라, 흩어지면 상태 변화를 추적할 수 없다. 기본은 `operator.add`이고, 커스텀은 `merge_sections` 하나로 충분하다 — 같은 `key`가 두 번 오면 나중 것이 이긴다(fork·재실행 시 필요).

### 서브그래프 4슬롯

```
validate_input ──→ process ──→ generate_output ──→ (ReportSection)
      │ 실패          │ 실패          │ 실패
      └──────────────┴──────────────┴──→ handle_error ──→ (degraded ReportSection)
```

**한 서브그래프의 실패가 리포트 전체를 죽이지 않는다.** `handle_error`는 예외를 잡아 "이 분석은 실패했음" 섹션을 만들고 나머지는 계속 돈다. 8시 리포트가 아예 안 오는 것보다 한 섹션이 비어서 오는 게 낫다.

`handle_error`는 `Command`로 에러 기록과 다음 이동을 한 번에 커밋한다(memo.md의 "Command: 상태 업데이트도 함께 — 원자성").

### 병렬과 취합

```
       ┌→ analysis.latency ─┐
START ─┼→ kafka.lag         ─┼→ aggregate → render → deliver → END
       └→ analysis.error_rate┘
```

LangGraph가 fan-out을 자동 병렬 실행하고, 취합 노드는 전부 끝나야 도는 자연스러운 barrier가 된다.

**`Send` API는 동적 팬아웃일 때만** 쓴다 — "컨슈머 그룹이 실행 시점에 20개로 밝혀졌고 각각 분석" 같은 경우. config로 목록이 고정된 서브그래프에는 필요 없다.

### Time Travel

별도 구현이 없다. 체크포인터가 Mongo에 있으므로 `thread_id`로 과거 실행을 찾아 특정 체크포인트에서 fork하면 된다. 프롬프트만 바꿔 재실행하는 것도 여기서 나온다.

## 5. 수집 계층

### 포트는 역할 기반, 그러나 템플릿이 미리 정하지 않는다

`MongoPort`, `RedisPort`처럼 기술 이름으로 포트를 만들면 서브그래프가 "나는 Mongo에서 읽는다"를 알게 되고, 저장소를 바꾸는 순간 서브그래프를 고쳐야 한다. 포트는 역할 기반이어야 한다.

그런데 역할 기반 포트는 정의상 도메인을 알아야 나온다. 도메인 무관 템플릿이 이를 미리 확정하면 추측이 되고, 실제 프로젝트에서 안 맞으면 우회된다. 그래서 책임을 나눈다.

| | 누가 정의 | 무엇 |
|---|---|---|
| 도메인 포트 | 각 프로젝트 | `LatencyMetricPort` 등 — 역할 기반 |
| 기반 | 템플릿 | 그걸 쉽게 만들 수 있는 재료 |

**템플릿이 제공하는 것:**

1. **`BaseAdapter` 4종** — mongo/redis/kafka_admin/rest. 타임아웃·재시도(지수 백오프)·커넥션 풀 수명 관리 내장
2. **포트 정의 규약** — `Protocol` + ctx 타입(`HistoricalContext`/`SnapshotContext`)으로 시간 계약을 타입에 박는 방식
3. **바인딩 메커니즘** — config에서 포트↔어댑터를 잇고 `Dependencies`로 주입
4. **레퍼런스 포트 1개** — `MetricPort` + Mongo/REST 두 어댑터. 새 포트를 만들 때 복붙 출발점

포트↔어댑터 바인딩은 config에서 한다. 아래는 프로젝트가 도메인 포트를 정의한 뒤의 모습이며, 템플릿이 기본 제공하는 것은 `metric` 한 줄이다.

```json
{ "ports": { "metric": "mongo" } }
```

GBM/FCT마다 같은 지표가 다른 저장소에 있어도(`"metric": "rest"`) 서브그래프 코드는 그대로다.

새 포트 추가는 `Protocol` 정의 몇 줄 + `BaseAdapter` 상속으로 끝난다. 신뢰성 코드(타임아웃·재시도·풀)를 다시 쓰지 않는다.

### 어댑터

모두 async이고 각자 원본 → 도메인 변환을 내장한다. 이 변환은 별도 계층이나 "Mapper"가 아니라 어댑터의 사적인 구현 디테일이다 — Mongo 문서 구조를 아는 유일한 곳이 Mongo 어댑터이기 때문이다.

| 어댑터 | 라이브러리 | 비고 |
|---|---|---|
| mongo | motor | historical 조회의 주력 |
| redis | redis.asyncio | 스냅샷·캐시 |
| kafka_admin | aiokafka | **메시지 소비 안 함** — 오프셋 메타데이터만 |
| rest | httpx.AsyncClient | 타임아웃·재시도 필수 |

**타임아웃 초과는 해당 서브그래프의 `handle_error`로 흘러간다.** 외부 시스템은 반드시 느려지거나 죽으므로, 여기서 막지 않으면 8시 배치가 하루 종일 매달린다.

**연결 수명**: 커넥션 풀은 프로세스 시작 시 한 번 만들어 `Dependencies`에 담고 조립 시점에 주입한다. 노드가 매번 연결을 열면 상주 스케줄러 환경에서 커넥션이 샌다.

### Kafka의 두 얼굴

| | 무엇 | 어디에 |
|---|---|---|
| 메시지 스트림 | 데이터 원천 | **템플릿 범위 밖** — 별도 인제스터가 Mongo에 적재 |
| lag·파티션·그룹 상태 | 분석 대상 지표 | AdminClient 기반. Redis/Mongo/REST와 나란한 소스 |

historical 조회는 Kafka가 아니라 **Mongo에서** 한다. Kafka의 `offsets_for_times`로 구간 소비가 기술적으로 가능하긴 하지만, retention 밖은 조회 불가이고, 인덱스도 필터도 없어 구간 전체를 전량 소비해야 하며, 파티션별 seek과 타임스탬프 의미(`CreateTime`/`LogAppendTime`) 문제가 따라온다.

## 6. LLM과 관측

### 배치

취합 단계와 각 서브그래프의 process 양쪽에서 LLM을 쓴다. 단, **디버깅 가능한 구조가 전제 조건**이다.

- 세부 요약: 각 서브그래프의 `generate_output`이 자기 결과만 보고 작성. 병렬, 컨텍스트 작음
- 전체 요약: 취합 노드가 모든 결과를 보고 1회 작성

**숫자는 LLM이 다시 쓰지 않는다.** "p95가 412ms"를 문장으로 옮기게 하면 언젠가 421ms로 쓴다. 숫자는 템플릿이 State에서 직접 렌더링하고 LLM은 서술만 담당한다.

### 판정에 근거를 강제

```python
class Judgement(BaseModel):
    verdict: Literal["normal", "warning", "critical"]
    reasoning: str
    evidence: list[RecordRef]     # 실제 입력 record의 id
    confidence: float
```

`evidence`가 실제 record id를 가리키므로 리포트의 어떤 문장이든 원본 데이터까지 역추적되고, 환각도 잡힌다.

### 환각 가드레일

LLM이 반환한 `evidence` id가 실제 입력에 존재하는지 코드로 대조한다. 없으면 재시도, 재시도도 실패하면 그 판정을 버리고 degraded 섹션으로. LLM 없이 도는 결정론적 검증이라 항상 켠다.

### 단일 호출 경로와 기록

LLM 호출은 전부 어댑터 하나를 거친다. 프롬프트 렌더링 → 호출 → 기록 → structured output 파싱.

```
노드 → LLMPort ─→ [replay 캐시 히트?] ─예→ 저장된 응답 반환
                        │아니오
                        └→ 실제 호출 → LLMTrace 기록
```

`LLMTrace`는 **변수 치환이 끝난 최종 프롬프트**, raw 응답, model, temperature를 담는다. State에 쌓이므로 Mongo 체크포인트에 자동 저장되고, 별도 로깅 인프라가 필요 없다.

### replay 모드

`--replay <thread_id>`로 저장된 LLM 응답을 재생한다. "리포트가 이상한데 LLM 판단이 틀린 건지 취합 로직이 틀린 건지"를 분리하는 용도이며, 노드 캐시 데코레이터와 같은 메커니즘 위에 올라간다.

### 공급자 교체

사내망을 기본으로 하되 어느 쪽이든 갈아끼울 수 있게 한다. config에 `provider`/`model`/`base_url`을 두고 비밀값은 `.env`에서 읽는다. 사내 게이트웨이가 OpenAI 호환 엔드포인트면 `base_url` 교체만으로 끝난다.

### 스트리밍

`stream_mode="updates"`로 노드별 진행을 흘려보낸다. CLI에서는 진행 표시, 스케줄러에서는 로그.

## 7. 리포트와 발송

### 공통 출력 스키마

모든 서브그래프가 같은 스키마를 반환하므로 템플릿이 서브그래프를 하나도 몰라도 된다.

```python
class ReportSection(BaseModel):
    key: str                     # "analysis.latency"
    title: str
    severity: Severity           # 정렬·강조용
    narrative: str               # LLM 서술
    metrics: list[Metric]        # 숫자 — 템플릿이 직접 렌더링
    judgements: list[Judgement]  # 전체 요약의 재료 + 근거 추적
```

```jinja
## 전체 요약
{{ overall_narrative }}

{% for s in sections %}
## {{ s.title }}
{{ s.narrative }}
{{ render_metrics(s.metrics) }}
{% endfor %}
```

서브그래프를 config에서 끄면 섹션이 자동으로 사라진다. 템플릿을 고칠 일이 없다.

### 전체 요약의 재료

구조화된 `judgements`와 **사전 계산된 집계치**를 넘긴다. 세부 요약 문장을 다시 요약하면 정보 손실이 2단 누적되고, 무엇보다 "critical 몇 건"을 정확히 세지 못한다. 개수·심각도는 코드가 계산해 넘기고 LLM은 해석과 우선순위만 맡는다.

### 계층 경계

| | 누가 | 무엇을 |
|---|---|---|
| application | `generate_output` 노드 | `ReportSection` **객체**를 만듦 |
| adapters/driven | renderer | 그 객체들을 md **문자열**로 |
| adapters/driven | delivery | 그 문자열을 SMTP/파일로 **내보냄** |

노드는 `ReportRendererPort`만 알고 Jinja2는 모른다. "md 대신 HTML 메일"은 renderer 추가 + config 한 줄이 된다.

### 발송 채널과 멱등성

발송 채널(파일/메일/추후 추가)은 config로 on/off한다. GBM/FCT별로 수신자와 채널이 다를 수 있다.

**멱등키**는 State의 `delivered` 필드로 관리한다. 체크포인터가 이미 Mongo에 있으므로 별도 컬렉션이나 의존성이 필요 없다 — 재개하며 State를 복원하면 `delivered`도 함께 살아나고, 발송 노드는 그것을 보고 skip한다.

인메모리 관리는 불가능하다. 프로세스가 죽으면 메모리도 죽고 재개는 새 프로세스에서 일어나므로, 정확히 막으려던 케이스를 못 막는다. 같은 프로세스 안의 중복 실행은 체크포인터가 이미 막고 있어 인메모리 멱등키는 실질 보호가 0이다.

남는 틈은 하나다 — 발송 직후 체크포인트 커밋 직전에 죽는 경우. 외부 시스템이 끼어 있어 완전한 원자성은 불가능하고, 하루 1회 배치에서 그 확률 대비 중복 메일 1통의 피해가 작으므로 더 투자하지 않는다.

## 8. 실행과 스케줄링

### 체크포인터

**MongoDB.** 이미 쓰고 있고, 영속적이며, 과거 실행 이력을 쿼리로 뒤질 수 있다. Durable Execution과 Time Travel의 토대이므로 인메모리 saver는 선택지가 아니다(프로세스 재시작 후 재개가 원천적으로 불가능).

`judgements`·`delivered`·`traces`가 모두 State에 있으므로 한곳에 남는다.

### 진입점

```bash
python -m app run --gbm mx --factory gumi                            # as_of = 지금
python -m app run --gbm mx --factory gumi --as-of 2026-08-12T08:00   # 재실행
python -m app run ... --replay <thread_id>                           # LLM 고정 재현
python -m app config show --gbm mx --factory gumi                    # 병합 결과 + 출처
python -m app scheduler                                               # 상주 모드
```

### 스케줄러

내장 스케줄러(APScheduler 등)를 상주 모드로 돌린다. cron 표현식은 config에 두어 GBM/FCT별 주기를 한곳에서 관리한다.

스케줄러는 `adapters/driving/`에 산다. **스케줄러는 유스케이스를 호출할 뿐 유스케이스가 아니다** — CLI가 유스케이스가 아닌 것과 같은 이유다. "매일 8시"라는 정책은 config 데이터이고, cron 파싱·타이머·콜백 디스패치는 기술 세부사항이다. APScheduler를 Celery beat나 k8s CronJob으로 바꿔도 정책은 바뀌지 않는다.

테스트가 이를 확인해준다. "8시에 도는가"를 검증하려면 시스템 시계를 조작해야 하는데 그건 APScheduler를 테스트하는 것이고, 리포트 생성은 `as_of`만 넘기면 시간 조작 없이 검증된다.

### 중복 실행 방지

Mongo에 `{gbm, factory, as_of}` 유니크 인덱스로 실행 락을 건다. 상주 프로세스가 둘 뜨거나(롤링 배포 중 겹침, HA 이중화) 이전 실행이 다음 스케줄을 넘겨 아직 돌고 있을 때, 두 번째 실행은 즉시 종료한다.

## 9. 하드코딩 금지

매직값은 상수로 올린다. 전역 상수는 `src/constants.py`, 모듈 지역 상수는 각 파일 상단.

## 10. 테스트 전략

| 층 | 방법 | 외부 의존 |
|---|---|---|
| 노드 | 가짜 포트 + 값 주입해 직접 호출 | 없음 |
| 서브그래프 | 4슬롯 흐름 + 실패 경로 검증 | 없음 |
| config | 3단 병합 결과와 검증 실패 케이스 | 없음 |
| 전체 그래프 | 가짜 포트 + 가짜 LLM으로 end-to-end | 없음 |
| 어댑터 | **작성하지 않음** (§11 참조) | — |

포트가 `Protocol`이고 config가 조립 시점에만 쓰이므로 모든 테스트가 외부 의존 없이 돈다.

## 11. 알려진 리스크와 보류한 결정

### 어댑터 테스트 부재 (의도적 결정)

어댑터 테스트를 작성하지 않기로 했다. 이로 인해 다음 부류의 버그가 **운영에서 처음 발견되는 경로가 열려 있다**:

- Mongo가 `Decimal128`을 돌려주는데 `float`으로 받는 등 타입 불일치
- 필드 부재·이름 불일치·null 혼입
- aggregation 문법 오류 (문법이 틀려도 파이썬은 dict를 잘 만든다 — 서버가 거부할 뿐)
- `aiokafka` AdminClient의 실제 반환 모양이 예상과 다른 경우

목(mock)으로는 이를 원리적으로 잡을 수 없다. 목은 우리 가정을 그대로 반영하므로 우리의 오해도 그대로 통과시킨다.

완화 경로: 실제 응답을 한 번 떠서 `tests/fixtures/`에 저장하는 **녹화 픽스처**를 추가하면 Docker 없이도 위 항목 중 앞 둘(가장 흔한 것)을 잡을 수 있다. 필요해지면 그때 도입한다.

### memo.md 참고 코드의 결함

`memo.md`의 `DeployConfig.py`는 그대로 옮기면 안 된다:

- `parent.parentnt`, `_depp_merge_dict`, `def _load_json(path: Path) _. dict`, `_load_merged_config(self) -> dict` 콜론 누락 — 오타
- `gbm_path`를 만들어놓고 미정의 변수 `common_path`를 읽음 — 실행 불가
- 주석은 3단 병합인데 코드는 2단만 병합, `factories/{factory}/common` 누락
- `_deep_merge_dict` docstring "update_dict is the first"가 실제 동작과 혼동을 준다

### memo.md 대비 변경 사항

| memo.md 항목 | 결정 |
|---|---|
| "Mapper가 존재해서 노드별 + GBM/FCT별 매핑" | **삭제.** GBM/FCT 분기는 config 슬롯 override로, 원본→도메인 변환은 어댑터 내부 구현 디테일로 흡수 |
| 데이터 소스에 Kafka | **재분류.** 메시지 소비가 아니라 lag/상태 조회 대상 |
| "Report는 메일이나 md" | 유지. 채널은 config on/off |
| 병렬 실행 | LangGraph fan-out 자동 병렬 |
| Time Travel | 체크포인터로 무료 획득, 별도 구현 없음 |

## 12. 다음 단계

이 스펙을 구현 계획으로 옮긴다. 구현은 얇은 수직 슬라이스(config 로딩 → 더미 포트 → 서브그래프 1개 → 취합 → md 출력)를 먼저 관통시켜 계층 경계와 인터페이스를 검증한 뒤, 나머지 어댑터·채널·운영 기능을 얹는 순서를 권한다.
