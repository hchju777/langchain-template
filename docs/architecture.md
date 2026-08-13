# 아키텍처 — 무엇이 무엇을 하는가

**"실행하면 무슨 일이 일어나는가"**에 답하는 문서입니다.

- 설정 항목의 의미는 [config 레퍼런스](config-reference.md)
- 하고 싶은 일이 정해졌으면 [작업별 가이드](howto.md)
- 용어와 판단 기준은 [용어집](glossary.md)

---

## 1. 계층

**4계층이고 의존 방향은 안쪽으로만 향합니다. `domain`은 바깥을 모릅니다.**

```
      presentation                                infrastructure
   (사람과 맞닿는 면)                              (외부 세계 I/O)
   ┌──────────────┐                            ┌──────────────────┐
   │ CLI          │──→  ┌─────────────┐   ──→  │ Mongo/Redis/REST │
   │ (controller) │     │ application │        │ Kafka Admin, LLM │
   ├──────────────┤     │  run_report │   ──→  │ 파일 쓰기, SMTP   │
   │ renderers    │←──  │      ↓      │        │ 체크포인터·락     │
   │ templates    │     │   domain    │   ──→  │ Scheduler ───┐   │
   │ (presenter)  │     └─────────────┘        └──────────────│───┘
   └──────────────┘            ↑                              │
                               └──────────────────────────────┘
                          스케줄러도 같은 유스케이스를 부른다
```

```
src/
├── domain/                     ← 바깥을 모르는 순수 계층
│   ├── models.py                 Context 계열, Record, Judgement, ReportSection
│   ├── ports.py                  Protocol 정의 (구현은 바깥 두 계층에)
│   └── reducers.py               커스텀 리듀서 — 한곳에 모은다
│
├── application/                ← 유스케이스
│   ├── usecase.py                run_report — CLI와 스케줄러의 공통 진입점
│   ├── graph/
│   │   ├── state.py              ReportState, Dependencies
│   │   ├── builder.py            Composition Root — config를 보는 유일한 곳
│   │   └── aggregate.py          취합 → 렌더 → 발송 노드
│   ├── subgraphs/              ← 자동 스캔 대상. 경로가 곧 등록명
│   │   ├── base.py               BaseSubgraph (4슬롯)
│   │   └── <영역>/<이름>.py       → "<영역>.<이름>"
│   ├── nodes/                  ← 공유 슬롯 부품
│   └── decorators.py             with_timing / with_error_handling / with_cache
│
├── presentation/               ← 무엇을 어떻게 보여줄 것인가
│   ├── cli.py                    인자 → BaseContext (controller), 결과 표시
│   ├── renderers.py              ReportSection → md 문자열 (presenter)
│   └── templates/                리포트 양식. config로 교체
│
├── infrastructure/             ← 외부 세계와의 I/O
│   ├── base.py                   BaseAdapter — 타임아웃·재시도
│   ├── stores.py                 Redis/Mongo/Kafka/REST 어댑터
│   ├── router.py                 DataRouter — kind → 어댑터
│   ├── checkpoint.py             재개·Time Travel의 저장 백엔드
│   ├── scheduler.py              APScheduler 상주 모드
│   ├── lock.py                   RunLock — 중복 실행 방지
│   ├── llm.py                    LLM 단일 경로 + 가드레일 + replay
│   ├── delivery.py               파일 쓰기 / SMTP 발송
│   └── fake_data.py              ★ 실제 구현에서는 사라짐
│
├── config/                     ← 설정을 "읽는 코드"
│   ├── loader.py                 3단 deep merge + 출처 추적
│   ├── env.py                    EnvConfig — 접속 정보·비밀값
│   └── registry.py               자동 스캔 + @register + 부팅 검증
│
└── constants.py                  매직값 금지
```

`src/config/`(코드)와 최상위 `config/`(JSON 데이터)는 이름이 같지만 다릅니다. 전자가 후자를 읽습니다.

### presentation과 infrastructure의 경계

가장 헷갈리는 지점이 **리포트 출력**입니다. "만들어서 파일로 저장하고 메일로 보내는 것"이 한 덩어리처럼 보이지만 **갈라집니다.**

| | 하는 일 | 계층 |
|---|---|---|
| `renderers.py` | `ReportSection` → md **문자열** | presentation |
| `templates/` | 어떤 양식으로 보일지 | presentation |
| `FileDelivery` | 그 문자열을 **디스크에 쓰기** | infrastructure |
| `MailDelivery` | 그 문자열을 **SMTP로 보내기** | infrastructure |

판별 기준은 하나입니다 — **"바꾸면 사용자가 보는 내용이 달라지나, 도착 경로만 달라지나?"**

`MailDelivery`는 리포트에 무엇이 쓰여 있는지 **전혀 모릅니다.** 완성된 문자열을 소켓에 밀어넣을 뿐이라 파일 쓰기와 같은 일입니다.

같은 이유로 **CLI와 스케줄러도 갈라집니다** — CLI는 사람이 명령하고 사람이 결과를 보므로 presentation, 스케줄러는 시간 트리거라 infrastructure입니다.

### 축이 셋입니다

흔한 오해 — "presentation은 출력, infrastructure는 입력"이 아닙니다. **둘 다 입력과 출력을 각각 갖습니다.**

|  | 들어옴 | 나감 |
|---|---|---|
| **presentation** (상대가 **사람**) | `cli.py` 인자 파싱 | `renderers.py` + `templates/` |
| **infrastructure** (상대가 **기계**) | `stores.py` 조회 | `delivery.py` 파일·SMTP |

그리고 DB 조회는 데이터로는 입력이지만 Hexagonal에서는 `driven`입니다 — 데이터가 들어와도 **호출은 우리가 하기** 때문입니다.

| | 데이터 방향 | 호출 방향 | 상대 |
|---|---|---|---|
| CLI 인자 | 들어옴 | **driving** | 사람 |
| DB 조회 | 들어옴 | **driven** | 기계 |
| 리포트 렌더 | 나감 | driven | 사람 |
| 메일 발송 | 나감 | driven | 기계 |

폴더 구조는 그중 **상대**를 표현합니다.

| Clean 링 | 이 저장소 |
|---|---|
| Entities | `domain/models.py` |
| Use Cases | `application/` |
| Interface Adapters | `presentation/`(controller·presenter) + `infrastructure/`(gateway) |
| Frameworks & Drivers | `infrastructure/` 안의 pymongo·httpx·aiokafka 호출부 |

---

## 2. 한 번 실행의 전 과정

`python -m src run --gbm mx --factory gumi`를 쳤을 때 일어나는 일입니다.

```
① CLI          인자 파싱 → gbm/factory/as_of 확정
                (--as-of 없으면 now(). 이후 아무도 now()를 부르지 않는다)
       ↓
② RunLock      thread_id로 파일 락 획득 — 실패하면 여기서 종료(rc=3)
       ↓
③ run_report   ← 스케줄러도 여기로 들어온다
       ↓
④ DeployConfig 3단 merge + 값별 출처 기록
       ↓
⑤ build_dependencies      어댑터 생성 → ports 라우팅 → LLM → 렌더러 → 발송 채널
       └ 검증: ports 어댑터 존재 / kind 지원 / 템플릿 파일 존재
       ↓
⑥ build_graph             레지스트리 대조 → 서브그래프 조립 → 체크포인터 부착
       └ 검증: 이름 대조 / 스키마 / 슬롯 참조 / 데이터 경로
       ↓
⑦ graph.ainvoke(thread_id)
       ├→ 서브그래프 N개 병렬 (각각 4슬롯)
       ├→ aggregate   판정 모으기 → 집계는 코드가 → LLM이 전체 요약
       ├→ render      ReportSection[] → md 문자열
       └→ deliver     delivered 확인 후 파일·메일
       ↓
⑧ deps.close()  커넥션 정리 (finally)
       ↓
⑨ RunLock 해제 (finally)
       ↓
⑩ CLI          리포트·발송·가드레일·부분실패 출력, --save-traces/--show-checkpoints
```

**②가 ③보다 먼저인 게 중요합니다.** 락을 먼저 잡아야 조립 비용을 치르기 전에 중복 실행을 막습니다.

**⑤와 ⑥ 사이에 검증이 나뉘어 있습니다.** ⑤는 "인프라를 만들 수 있나", ⑥은 "config와 코드가 맞나"를 봅니다. 둘 다 실패하면 **그래프가 한 번도 돌지 않고** 종료 코드 2로 끝납니다.

**⑧⑨는 `finally`입니다.** 실패해도 커넥션과 락이 남지 않습니다.

---

## 3. 컴포넌트별 책임

### `run_report` — 유스케이스

| | |
|---|---|
| 무엇을 | 조립 → 실행 → 결과 반환 |
| 언제 | CLI의 `run`, 스케줄러의 트리거 |
| 의존 | `DeployConfig`, `build_dependencies`, `build_graph` |
| 없으면 | CLI와 스케줄러가 같은 로직을 각자 갖게 됨 |

`ReportRun`(state, thread_id, graph, run_config)을 돌려줍니다. 호출자는 `run.rendered`, `run.errors`처럼 꺼내 쓰고, Time Travel이 필요하면 `run.graph`를 씁니다.

**부팅 검증 실패를 잡지 않고 그대로 올립니다.** CLI는 사람이 읽는 메시지로, 스케줄러는 로그로 각자 보고하기 때문입니다.

### `DeployConfig` — 설정 로더

| | |
|---|---|
| 무엇을 | 3단 JSON deep merge + 값마다 출처 기록 |
| 언제 | `run_report` 시작 시 1회 |
| 의존 | `config/` 디렉터리 |
| 없으면 | GBM/FCT별 차이를 코드로 분기해야 함 |

`origins` 딕셔너리가 `config show`의 출처 표시를 만듭니다. 없는 계층은 조용히 건너뛰므로 **오타 난 factory도 에러가 아닙니다** — `config show`로 `○`/`×`를 확인해야 합니다.

### 레지스트리 (`config/registry.py`)

| | |
|---|---|
| 무엇을 | 서브그래프·노드 자동 발견, 부팅 검증 4종 |
| 언제 | 첫 호출 시 1회 스캔 (`discover`), 조립 시 검증 |
| 의존 | `application/subgraphs/`, `application/nodes/` 패키지 |
| 없으면 | 새 분석마다 조립 코드를 손으로 고쳐야 함 |

모듈 경로가 등록명이 됩니다(`kafka/lag.py` → `kafka.lag`). `slot_source()`가 슬롯 override 대상을 찾아주는데, **노드 부품과 다른 서브그래프 둘 다** 받습니다.

### `Dependencies` — 주입 묶음

| | |
|---|---|
| 무엇을 | 어댑터·LLM·렌더러·발송 채널을 한 묶음으로 |
| 언제 | `build_dependencies`에서 프로세스당 1회 |
| 의존 | `DeployConfig`, `EnvConfig` |
| 없으면 | 노드가 커넥션을 직접 만들어 상주 모드에서 샘 |

**기술 이름 필드가 없습니다** — `data`(라우터), `llm`, `renderer`, `health`, `deliveries`, `adapters`뿐입니다. 서브그래프는 `deps.data`만 알고 저장소는 모릅니다.

`close()`가 `adapters` 리스트를 순회하므로 **새 어댑터는 거기 등록되어야** 연결이 닫힙니다.

### `DataRouter` — kind → 어댑터

| | |
|---|---|
| 무엇을 | `FetchSpec.kind`로 어댑터를 골라 위임 |
| 언제 | 서브그래프가 `deps.data.fetch()`를 부를 때마다 |
| 의존 | config의 `ports` 매핑 |
| 없으면 | 서브그래프가 `deps.redis`처럼 저장소를 직접 알게 됨 |

**이것 하나가 "저장소 교체가 config 한 줄"을 성립시킵니다.** 라우팅만 하고 조회는 어댑터에 넘깁니다.

매핑에 없는 kind를 요청하면 `UnroutedKindError`인데, 실행 중에 그걸 만나지 않도록 **부팅에서 미리** 검사합니다(서브그래프의 `required_kinds` ↔ `ports`).

### 어댑터 (`stores.py` + `BaseAdapter`)

| | |
|---|---|
| 무엇을 | 외부 조회 + 원본 → `Record` 변환 |
| 언제 | 라우터가 위임할 때 |
| 의존 | 실제 서버 (지금은 `fake_data`) |
| 없으면 | 서브그래프가 드라이버 API를 직접 다뤄야 함 |

`BaseAdapter._call`이 **타임아웃과 지수 백오프 재시도**를 씌웁니다. 타임아웃 초과는 예외로 올라가 그 서브그래프의 `handle_error`로 흘러가므로, **한 저장소가 느려도 나머지 분석은 계속됩니다.**

`supported_kinds`는 "이 어댑터가 다룰 줄 아는 것"의 선언이고, config가 지원하지 않는 kind를 보내면 부팅에서 막힙니다.

`_to_domain`은 **어댑터의 사적인 구현**입니다 — 원본 구조를 아는 유일한 곳이라 그렇습니다.

### LLM 어댑터 (`llm.py`)

| | |
|---|---|
| 무엇을 | 모든 LLM 호출의 단일 경로. 기록·replay·가드레일 |
| 언제 | `generate_output`의 서술, `process`의 judge, 취합의 전체 요약 |
| 의존 | config의 `llm`, `.env`의 `LLM_BASE_URL`/`LLM_API_KEY` |
| 없으면 | 프롬프트 추적과 근거 검증이 노드마다 흩어짐 |

`BaseLLMAdapter`가 공통(기록·replay·가드레일)을 갖고, `FakeLLMAdapter`/`ChatModelAdapter`가 실제 응답 생성만 다릅니다. **그래서 fake로 개발하다 실제로 바꿔도 관측·검증이 그대로입니다.**

서브그래프마다 다른 인스턴스를 가질 수 있어(`subgraphs.<name>.llm`), 기록을 어댑터에 쌓아두면 한곳에서 못 모읍니다. 그래서 `drain_traces()`/`drain_guardrail_drops()`로 **State에 올려보냅니다.**

자세한 건 [LLM 규약과 연결](llm.md)을 보세요.

### 체크포인터 (`checkpoint.py`)

| | |
|---|---|
| 무엇을 | 노드마다 State 저장 → 재개·Time Travel·fork |
| 언제 | `build_graph`에서 `compile(checkpointer=...)` |
| 의존 | config의 `checkpoint.backend`, (mongodb면) `.env`의 `MONGODB_URI` |
| 없으면(`none`) | 리포트는 정상. 이력 조회·fork 불가 |

**노드도 State도 이 결정을 모릅니다** — `compile()`에 넘기는 인자일 뿐입니다.

우리 Pydantic 모델을 복원하려면 허용 타입을 등록해야 하는데, 도메인 타입은 자동 수집하고 `ReportState`는 **조립 시점에 주입**받습니다. `infrastructure`가 `application`을 import하면 의존 방향이 뒤집히기 때문입니다.

`MongoDBSaver`는 생성 시점에 인덱스를 만들려고 **즉시 연결**합니다. 서버가 없으면 부팅이 실패하는데, 저장 안 되는 채로 도는 것보다 낫습니다.

### `RunLock` (`lock.py`)

| | |
|---|---|
| 무엇을 | 같은 `{gbm}:{factory}:{as_of}`의 동시 실행 차단 |
| 언제 | CLI `run` 시작 시, 스케줄러 트리거 시 |
| 의존 | `.locks/` 디렉터리 |
| 없으면 | 배치와 수동 실행이 겹쳐 리포트가 두 번 나감 |

`fcntl`은 Windows에 없어 **원자적 파일 생성**(`O_CREAT|O_EXCL`)을 씁니다. 두 OS에서 원자적이고 표준 라이브러리만 필요합니다.

6시간 지난 락은 죽은 프로세스가 남긴 것으로 보고 강탈합니다 — 그러지 않으면 크래시 한 번이 스케줄을 영구히 막습니다.

**같은 호스트 안에서만 유효합니다.** 여러 호스트라면 공유 저장소 기반 락이 필요합니다.

### 스케줄러 (`scheduler.py`)

| | |
|---|---|
| 무엇을 | cron 시각에 `run_once` 호출 |
| 언제 | `python -m src scheduler` 상주 모드 |
| 의존 | APScheduler, config의 `schedule` |
| 없으면 | 외부 cron이나 k8s CronJob에서 CLI를 부르면 됨 |

**유스케이스가 아닙니다.** 시간이 되면 `run_report`를 부를 뿐이고, 그건 CLI가 하는 일과 같습니다.

`max_instances=1`이 같은 프로세스 안의 중복을, `RunLock`이 프로세스 간 중복을 막는 **이중 방어**입니다.

APScheduler는 `cli.py`에서 **지연 import**됩니다 — 최상단에서 부르면 패키지가 없을 때 `registry`·`run`까지 죽습니다.

### 렌더러 (`renderers.py`)

| | |
|---|---|
| 무엇을 | `ReportSection[]` → md 문자열 |
| 언제 | `render` 노드 |
| 의존 | `templates/`의 양식 파일 |
| 없으면 | 노드가 마크업을 직접 조립하게 됨 |

노드는 `ReportRendererPort`만 알고 템플릿 문법도 파일도 모릅니다. **숫자는 여기서 State의 값을 직접 찍습니다** — LLM이 다시 쓰지 않습니다.

### 발송 (`delivery.py`)

| | |
|---|---|
| 무엇을 | 완성된 문자열을 파일·SMTP로 |
| 언제 | `deliver` 노드 |
| 의존 | config의 `delivery`, `.env`의 SMTP |
| 없으면 | 리포트는 만들어지지만 아무 데도 안 감 |

**멱등키가 State의 `delivered`에 있습니다.** 체크포인터에 함께 저장되므로 재개해도 같은 채널로 두 번 나가지 않습니다.

---

## 4. 여러 컴포넌트를 꿰는 것

### `as_of`

**밖에서 주입되고, 노드 안에서 `datetime.now()`를 부르지 않습니다.** 이 하나가 네 가지를 동시에 지탱합니다.

| | `as_of`가 고정이라 가능한 것 |
|---|---|
| 재현성 | 같은 `as_of`는 같은 리포트 |
| 구간 계산 | config의 `"P7D"` → 절대 시각 쌍 |
| Time Travel | 과거 실행을 날짜로 찾아감 |
| 중복 방지 | 락 키의 일부 |

노드가 `now()`를 부르면 재개 시 앞뒤 노드가 서로 다른 시점을 보게 되어 **전부 무너집니다.**

### `thread_id`

`{gbm}:{factory}:{as_of}` 하나가 **두 컴포넌트에서 쓰입니다.**

```
thread_id ─┬→ 체크포인터  이 실행의 State 이력을 묶는 키
           └→ RunLock     이 실행의 중복을 막는 키
```

그래서 "같은 실행"의 정의가 두 기능에서 자동으로 일치합니다.

### 부팅 검증 6종

두 군데로 나뉘어 돕니다.

| 어디 | 검사 |
|---|---|
| `build_dependencies` | ① `ports`의 어댑터 이름이 실재하나 ② 그 어댑터가 그 kind를 지원하나 ③ 템플릿 파일이 있나 |
| `validate_config` | ④ 이름 대조·고아 ⑤ 스키마(키 오타 포함) ⑥ 슬롯 참조 ⑦ 데이터 경로(`required_kinds` ↔ `ports`) |

`validate_config`는 문제를 **모아서 한 번에** 던집니다 — 3단 merge 환경에서 하나씩 고치며 재시작하는 건 고통스럽습니다.

CLI는 이 실패들을 `BOOT_ERRORS`로 잡아 `✗` 메시지와 종료 코드 2로 바꿉니다. **새 검증을 추가하면 그 목록에도 넣어야** raw traceback이 새지 않습니다.

### State

```python
class ReportState(BaseModel):
    ctx: BaseContext                                          # 불변
    sections:  Annotated[list[ReportSection],  merge_sections]
    errors:    Annotated[list[SubgraphError],  operator.add]
    traces:    Annotated[list[LLMTrace],       operator.add]
    guardrail_drops: Annotated[list[str],      operator.add]
    overall:   OverallSummary | None = None
    rendered:  str | None = None
    delivered: Annotated[list[DeliveryRecord], operator.add]
```

**리듀서가 병렬 서브그래프의 결과를 합치는 규칙**입니다. `domain/reducers.py` 한곳에 모읍니다 — 흩어지면 상태 변화를 추적할 수 없습니다.

`traces`·`guardrail_drops`가 State에 있는 이유는 서브그래프마다 LLM이 다를 수 있어서고, `delivered`가 State에 있는 이유는 재개 시 함께 복원되어야 해서입니다.

---

## 5. 서브그래프 4슬롯

```
validate_input ──→ process ──→ generate_output ──→ (ReportSection)
      │ 실패          │ 실패          │ 실패
      └──────────────┴──────────────┴──→ handle_error ──→ (degraded 섹션)
```

**한 서브그래프의 실패가 리포트 전체를 죽이지 않습니다.** 8시 리포트가 아예 안 오는 것보다 한 섹션이 비어서 오는 게 낫습니다.

슬롯 간 이동은 **전부 `Command`가 결정합니다.** 정적 엣지를 남기면 `Command(goto=handle_error)`가 그것을 대체하지 않고 *추가로* 동작해서, 실패 시 다음 슬롯과 `handle_error`가 같은 스텝에 함께 돌며 충돌합니다.

| 슬롯 | 기본 동작 | 보통 |
|---|---|---|
| `validate_input` | `BaseContext` → 자기 입력 스키마. `window` → 절대 시각 | 그대로 |
| `process` | — | **여기만 구현** |
| `generate_output` | LLM 서술 + `ReportSection` 조립 | 그대로 |
| `handle_error` | degraded 섹션 + 에러 기록 | 그대로 |

---

## 6. 핵심 규약 요약

| 규약 | 이유 |
|---|---|
| `as_of`를 밖에서 주입 | 재현성·재개·Time Travel·락이 전부 여기 걸림 |
| 숫자는 `Metric`에, LLM은 서술만 | LLM이 숫자를 옮기면 언젠가 틀리게 씀 |
| 판정에 `evidence`(실제 Record id) 강제 | 역추적 + 환각 가드레일 |
| config는 조립 시점에만 | 노드 시그니처가 요구사항을 문서화하고 테스트가 쉬워짐 |
| 슬롯 간 `Command`만 | 정적 엣지와 겹치면 실패 격리가 깨짐 |
| 리듀서는 한 파일에 | State 변화를 한곳에서 추적 |
| 부팅에서 막을 수 있으면 막는다 | 새벽 배치에서 발견되면 늦음 |
| 커넥션은 프로세스당 1회 | 상주 모드에서 새지 않게 |

---

## 더 보기

- 설정 항목 전체 — [config 레퍼런스](config-reference.md)
- LLM 규약과 연결 — [llm.md](llm.md)
- 새 분석 만들기 — [튜토리얼](tutorial.md)
- LangGraph 개념 자체 — [ref/](../ref/README.md)
