# config 레퍼런스

`config/*.json`의 모든 항목을 설명합니다. 이 문서는 코드에서 실제로 읽는 키만 다루며, 읽지 않는 것은 그렇다고 표시했습니다.

- 개념 설명과 사용 예는 [README](../README.md)
- 새 분석을 추가하며 배우려면 [튜토리얼](tutorial.md)

---

## 파일 구조와 병합

```
config/
├── gbm/{gbm}.json                     ① 사업부 기본값
└── factories/{factory}/
    ├── common.json                    ② 그 공장의 모든 GBM 공통
    └── {gbm}.json                     ③ 그 공장 + 그 GBM 전용
```

**①→②→③ 순으로 deep merge**되고 뒤가 앞을 덮어씁니다. 중첩 객체는 재귀적으로 병합되므로 **바꿀 키만** 적으면 됩니다.

```json
// ① gbm/mx.json
{ "subgraphs": { "kafka.lag": { "enabled": true, "warn_lag": 5000, "critical_lag": 30000 } } }

// ③ factories/gumi/mx.json
{ "subgraphs": { "kafka.lag": { "critical_lag": 20000 } } }

// 결과: enabled=true, warn_lag=5000, critical_lag=20000
```

파일이 없으면 그 계층은 건너뜁니다. **존재하지 않는 factory를 지정해도 에러가 아니라** gbm 계층만 적용되니, 의도한 파일이 읽혔는지는 진단 명령으로 확인하세요.

```bash
python -m src config show --gbm mx --factory gumi
```

```
## 계층 (뒤가 앞을 덮어씀)
  ○ gbm              .../config/gbm/mx.json
  ○ factory_common   .../config/factories/gumi/common.json
  ○ factory_gbm      .../config/factories/gumi/mx.json      ← ○ 읽힘 / × 없음

## 값의 출처
  subgraphs.kafka.lag.critical_lag       ← factory_gbm
```

### 여기에 넣지 말아야 할 것

**비밀값과 접속 주소.** JSON은 git에 커밋됩니다. 비밀번호·토큰·DSN은 `.env`로 갑니다 ([.env.example](../.env.example) 참조).

기준은 **자르는 축**입니다 — config는 GBM/FCT별로, `.env`는 배포 환경(dev/stg/prod)별로 갈립니다. 같은 `mx/gumi`라도 dev와 prod는 다른 Mongo를 보는데, 그건 3단 merge로 표현할 수 없습니다.

---

## 최상위 블록

| 블록 | 용도 | 필수 |
|---|---|---|
| [`subgraphs`](#subgraphs) | 어떤 분석을 켜고 어떻게 동작시킬지 | ✓ |
| [`ports`](#ports) | 어떤 데이터를 **어느 저장소**에서 가져올지 | ✓ |
| [`llm`](#llm) | LLM 어댑터·모델 | |
| [`report`](#report) | 리포트 양식 | |
| [`delivery`](#delivery) | 발송 채널 | |
| [`stores`](#stores) | 저장소 접근 정책 | |
| [`checkpoint`](#checkpoint) | 재개·Time Travel의 저장 백엔드 | |
| [`schedule`](#schedule) | 스케줄 — **현재 읽지 않음** | |

---

## `stores`

저장소 어댑터 공통 정책입니다.

| 키 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `timeout_sec` | float | `5.0` | 한 번의 조회에 허용할 시간(초). 초과하면 재시도 후 실패 |

```json
{ "stores": { "timeout_sec": 3.0 } }
```

타임아웃 초과는 **그 서브그래프의 `handle_error`로 흘러가** 해당 섹션만 실패 처리되고 나머지 분석은 계속됩니다. 외부 시스템은 반드시 느려지므로, 이 값이 8시 배치가 하루 종일 매달리는 걸 막습니다.

재시도 횟수와 백오프는 config가 아니라 `src/constants.py`의 `DEFAULT_MAX_RETRIES`(2), `DEFAULT_BACKOFF_BASE_SEC`(0.2)입니다.

> 접속 주소·계정은 여기가 아니라 `.env`입니다 (`REDIS_URL`, `MONGODB_URI` 등).

---

## `ports`

**무엇을 어디서 가져올지.** 키는 서브그래프가 요청하는 데이터 종류(`kind`)이고, 값은 어댑터 이름입니다.

```json
{
  "ports": {
    "production":       "redis",
    "line_info":        "redis",
    "equipment_status": "redis",
    "material_stock":   "redis",
    "consumer_lag":     "kafka",
    "kpi":              "rest",
    "alarms":           "mongodb"
  }
}
```

**서브그래프는 이 매핑을 모릅니다.** `kind`만 말하면 라우터가 어댑터를 찾습니다.

```python
records = await self.deps.data.fetch(state.scoped, FetchSpec(kind="material_stock"))
```

그래서 저장소를 옮길 때 **분석 코드를 고치지 않습니다** — config 한 줄이면 됩니다.

```json
{ "ports": { "material_stock": "rest" } }
```

### 쓸 수 있는 어댑터와 kind

각 어댑터는 자기가 다룰 수 있는 kind를 선언합니다.

| 어댑터 | 지원하는 kind |
|---|---|
| `redis` | `production`, `line_info`, `equipment_status`, `material_stock` |
| `mongodb` | `alarms` |
| `kafka` | `consumer_lag` |
| `rest` | `kpi`, `material_stock` |

**저장소 교체는 대상 어댑터가 그 kind를 지원할 때만 됩니다.** 아니면 부팅에서 막힙니다.

```
✗ 어댑터 'mongodb'은 'material_stock'을(를) 다루지 못합니다.
  이 어댑터가 지원하는 kind: alarms
```

새 kind를 추가하려면 어댑터의 `supported_kinds`에 넣고 조회 로직을 구현해야 합니다.

### 부팅 검증

| 상황 | 결과 |
|---|---|
| 켜진 서브그래프의 `kind`가 `ports`에 없음 | `서브그래프 'material.stock'이 요청하는 'material_stock'을(를) 어디서 가져올지 'ports'에 없습니다` |
| 없는 어댑터 이름 | `config의 'ports.kpi'가 가리키는 어댑터 '없는것'을 찾을 수 없습니다` |
| 어댑터가 그 kind를 지원 안 함 | 위 메시지 |

각 서브그래프가 어떤 kind를 요청하는지는 `python -m src registry`가 보여줍니다.

```
  material.stock           자재 소진 예상          ← material.stock.py
                           요청 데이터: material_stock
```

> **꺼둔 서브그래프의 kind는 매핑이 없어도 됩니다.** 검증은 `enabled: true`인 것만 봅니다.

---

## `llm`

| 키 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `adapter` | `"fake"` \| `"chat_model"` | `"fake"` | 가짜 응답 / 실제 LLM |
| `model` | str | `"fake-local"` | 모델 이름 |
| `temperature` | float | `0.0` | 샘플링 온도 |
| `provider` | str | `"openai_compatible"` | `chat_model`일 때만 사용 |
| `seed` | str | `""` | `fake`일 때만 사용 |

```json
{ "llm": { "adapter": "chat_model", "provider": "openai_compatible",
           "model": "gpt-4o-mini", "temperature": 0.0 } }
```

**`adapter`**
- `"fake"` — 규칙 기반 가짜 응답. 외부 통신 없음. LLM 없이 개발·테스트할 때
- `"chat_model"` — 실제 LLM. `langchain-openai` 같은 공급자 패키지가 이때만 로드됩니다

알 수 없는 값은 부팅 시 막힙니다: `ValueError: 알 수 없는 llm.adapter 'gpt'. 가능: fake, chat_model`

**`provider`**는 `PROVIDER_ALIAS`를 거쳐 `init_chat_model`이 아는 이름으로 매핑됩니다.

| config 값 | 실제 |
|---|---|
| `openai_compatible` | `openai` |
| `azure_openai` | `azure_openai` |
| 그 외 | 그대로 전달 |

사내 게이트웨이가 OpenAI 호환이면 `provider`는 `openai_compatible`로 두고 **`.env`의 `LLM_BASE_URL`만** 바꾸면 됩니다.

**`seed`**는 가짜 LLM의 난수원입니다. 현재 가짜 응답은 규칙 기반이라 출력이 바뀌지 않지만, GBM/FCT별로 다르게 두면 나중에 무작위성을 넣을 때 재현 가능한 구분자가 됩니다.

> `base_url`과 `api_key`는 config가 아니라 `.env`(`LLM_BASE_URL`, `LLM_API_KEY`)입니다. 환경별로 달라지기 때문입니다.

서브그래프별로 모델을 다르게 하려면 [`subgraphs.<name>.llm`](#subgraphsnamellm--모델-override)을 보세요.

---

## `report`

| 키 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `template` | str | `"report.md"` | `src/presentation/templates/` 아래 파일명 |

```json
{ "report": { "template": "report_brief.md" } }
```

기본 제공 양식:

| 파일 | 내용 |
|---|---|
| `report.md` | 전체 요약 + 섹션별 서술·지표 표·판정 근거 |
| `report_brief.md` | 전체 요약 + 섹션 한 줄 요약. 표와 근거 생략 |

파일이 없으면 **부팅 시 멈춥니다**: `✗ 템플릿 파일이 없습니다: .../report_typo.md`

양식 문법과 새 템플릿 만드는 법은 [README의 리포트 템플릿](../README.md#리포트-템플릿)에 있습니다.

---

## `delivery`

완성된 리포트를 어디로 내보낼지. 채널별로 켜고 끕니다.

```json
{
  "delivery": {
    "file": { "enabled": true },
    "mail": {
      "enabled": true,
      "recipients": ["gumi-ops@example.com"],
      "subject_prefix": "[구미 운영리포트]"
    }
  }
}
```

### `delivery.file`

| 키 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `enabled` | bool | `false` | md 파일 저장 |

저장 경로는 config가 아니라 `src/constants.py`의 `OUTPUT_ROOT`(프로젝트 루트의 `output/`)이고, 파일명은 `{gbm}_{factory}_{as_of}.md`입니다.

### `delivery.mail`

| 키 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `enabled` | bool | `false` | 메일 발송 |
| `recipients` | list[str] | `[]` | 수신자 — **공장별로 다르므로 여기에 둡니다** |
| `subject_prefix` | str | `"[운영리포트]"` | 제목 앞에 붙는 문자열 |

SMTP 서버 주소·계정은 `.env`(`SMTP_HOST`, `SMTP_USER`, `SMTP_PWD`)입니다. 같은 기능이 양쪽으로 갈리는 전형적인 예입니다 — 수신자는 공장별, 서버는 환경별.

> 채널을 하나도 안 켜면 리포트는 생성되지만 아무 데도 나가지 않습니다.

**멱등성**: 발송 이력은 State의 `delivered`에 남아 체크포인터에 저장되므로, 재개해도 같은 채널로 두 번 나가지 않습니다.

---

## `checkpoint`

노드가 끝날 때마다 State를 저장합니다. 이게 있어야 재개·Time Travel·fork가 됩니다.

| 키 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `backend` | `"memory"` \| `"none"` \| `"mongodb"` | `"memory"` | 저장 위치 |

```json
{ "checkpoint": { "backend": "memory" } }
```

| 값 | 동작 |
|---|---|
| `"memory"` | 프로세스 메모리. **Time Travel과 fork가 완전히 동작**하지만 재시작하면 사라집니다 |
| `"none"` | 체크포인트 없음. 리포트는 정상 생성되지만 이력 조회가 막힙니다 |
| `"mongodb"` | **미지원** — `langgraph-checkpoint-mongodb` 패키지가 필요한데 이 환경에 없습니다. 고르면 설치 안내와 함께 부팅이 멈춥니다 |

### thread_id

한 실행은 `{gbm}:{factory}:{as_of}`로 식별됩니다.

```
mx:gumi:20260813T0800
```

같은 `as_of`로 다시 돌리면 같은 스레드에 이어집니다. **`as_of`를 밖에서 주입하기로 한 결정이 여기서 값을 합니다** — 어제 실행을 날짜로 찾아갈 수 있습니다.

### 체크포인트 보기

```bash
python -m src run --gbm mx --factory gumi --show-checkpoints
```

```
체크포인트 (thread_id=mx:gumi:20260813T0800)
  step -1  다음: __start__                1f196bc9-c51b-...
  step  0  다음: health.connectivity, kafka.lag, ...  1f196bc9-c51c-...
  step  1  다음: aggregate                1f196bc9-c532-...
  step  2  다음: render                   1f196bc9-c533-...
  step  3  다음: deliver                  1f196bc9-c534-...
  step  4  다음: (완료)                    1f196bc9-c535-...
```

여기 나온 `checkpoint_id`로 특정 시점 State를 열거나(`aget_state`), 값을 바꿔 그 지점부터 다시 돌릴 수 있습니다(`aupdate_state` → `ainvoke`).

> **직렬화**: State에 우리 Pydantic 모델이 실리므로 복원 가능한 타입을 명시해야 합니다. `domain/models.py`의 클래스는 자동 수집되고, `ReportState`는 조립 시점에 주입됩니다 — `infrastructure`가 `application`을 import하면 의존 방향이 뒤집히기 때문입니다.

---

## `schedule`

상주 스케줄러가 언제 리포트를 돌릴지.

| 키 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `cron` | str | `"0 8 * * *"` | 표준 5필드 cron (분 시 일 월 요일) |
| `timezone` | str | `"Asia/Seoul"` | IANA 타임존 이름 |

```json
{ "schedule": { "cron": "0 8 * * *", "timezone": "Asia/Seoul" } }
```

```bash
python -m src scheduler --gbm mx --factory gumi
```

```
▶ 스케줄러 시작 — mx/gumi
cron '0 8 * * *' (Asia/Seoul)
  다음 실행: 2026-08-14 08:00:00 KST
  다음 실행: 2026-08-15 08:00:00 KST
```

배선만 확인하려면 스케줄을 기다리지 않고 즉시 한 번 돌릴 수 있습니다.

```bash
python -m src scheduler --gbm mx --factory gumi --once
```

### ⚠ 요일 숫자는 표준 cron과 다릅니다

|  | 0 | 1 | 2 | … | 6 |
|---|---|---|---|---|---|
| 표준 cron (crontab) | 일 | **월** | 화 | | 토 |
| APScheduler (여기) | 월 | **화** | 수 | | 일 |

**`0 8 * * 1`은 crontab에서 월요일이지만 여기서는 화요일입니다.** 자동 변환하지 않습니다 — `*/2`나 `1-5/2`처럼 스텝이 섞이면 변환 규칙이 지저분해지고 APScheduler 문서와도 어긋나기 때문입니다.

**이름을 쓰세요.** 그러면 애매함이 없습니다.

```json
{ "schedule": { "cron": "0 8 * * mon-fri" } }
```

숫자로 쓰면 부팅 시 경고가 뜹니다.

```
cron '0 8 * * 1'의 요일 '1'이 숫자입니다. APScheduler는 0=월요일이라
표준 cron(0=일요일)과 하루씩 어긋납니다. 이름으로 쓰는 편이 안전합니다.
```

### 중복 실행 방지

`{gbm}:{factory}:{as_of}`마다 **파일 락**을 잡습니다(`.locks/`). 두 가지를 막습니다.

- 상주 프로세스가 둘 이상 뜬 경우 (롤링 배포 중 겹침, HA 이중화)
- 이전 실행이 다음 스케줄을 넘겨 아직 도는 경우 — APScheduler의 `max_instances=1`이 같은 프로세스 안에서, 파일 락이 프로세스 간에서 막습니다

`python -m src run`도 같은 락을 씁니다. 배치가 도는 중에 손으로 같은 `as_of`를 돌리면 종료 코드 `3`으로 막힙니다.

6시간이 지난 락은 죽은 프로세스가 남긴 것으로 보고 강탈합니다.

> **파일 락은 같은 호스트 안에서만 유효합니다.** 여러 호스트에서 스케줄러를 띄운다면 Mongo의 `{gbm, factory, as_of}` 유니크 인덱스 같은 공유 저장소 기반 락이 필요합니다.

### 외부 cron을 쓴다면

상주 모드 대신 crontab이나 k8s CronJob에서 CLI를 직접 부르는 것도 됩니다. 그 경우 `schedule` 블록은 쓰이지 않습니다.

```bash
python -m src run --gbm mx --factory gumi
```

---

## `subgraphs`

어떤 분석을 켤지, 각각을 어떻게 동작시킬지. **이 블록이 이 config의 핵심입니다.**

```json
{
  "subgraphs": {
    "kafka.lag": { "enabled": true, "warn_lag": 5000 },
    "kpi.check": { "enabled": false }
  }
}
```

키는 **서브그래프 등록명**이고, 기본적으로 파일 경로에서 유도됩니다.

```
src/application/subgraphs/kafka/lag.py   →   "kafka.lag"
```

쓸 수 있는 이름은 `python -m src registry`로 확인하세요.

### 부팅 검증

config와 코드가 어긋나면 **프로세스가 뜨지 않고**, 문제를 전부 모아 한 번에 보고합니다.

| 검사 | 예시 메시지 |
|---|---|
| 이름 대조 | `config의 'subgraphs.kafka.lgg'이 레지스트리에 없습니다. 등록된 서브그래프: ...` |
| 고아 | `서브그래프 'x'가 등록되었지만 어느 config에도 없습니다` |
| 스키마 | `'subgraphs.kafka.lag.warn_lag': Input should be a valid integer (입력: '다섯천')` |
| 키 오타 | `'subgraphs.kafka.lag.warn_lagg': Extra inputs are not permitted (입력: 5000)` |
| 슬롯 참조 | `'...nodes.process'가 가리키는 'x'이 없습니다. 쓸 수 있는 이름: ...` |

**고아 검증** 때문에 등록된 서브그래프는 전부 어딘가의 config에 나타나야 합니다. 슬롯 부품으로만 쓰는 클래스는 코드에서 `@register(slot_only=True)`로 표시하면 제외됩니다.

---

### 모든 서브그래프의 공통 필드

| 키 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `enabled` | bool | `false` | **끄면 리포트에서 섹션이 사라집니다.** 템플릿은 안 고쳐도 됩니다 |
| `window` | ISO 8601 duration | `null` | 구간 분석의 기간. **구간 분석에는 필수** |
| `cache_ttl` | int \| null | `null` | 결과 캐시 유효시간(초). `null`이면 캐시 없음 |
| `nodes` | object | — | 슬롯 override ([아래](#subgraphsnamenodes--슬롯-override)) |
| `llm` | object | — | 이 서브그래프만의 LLM 설정 ([아래](#subgraphsnamellm--모델-override)) |

**`window`**는 `PT24H`(24시간), `P7D`(7일), `PT30M`(30분) 같은 ISO 8601 duration입니다. `as_of` 기준 **절대 시각 쌍**(`start_dt`/`end_dt`)으로 변환되므로 재실행해도 같은 구간을 봅니다.

- **구간 분석**(`context_type = HistoricalContext`)에 `window`가 없으면 그 서브그래프만 실행 중 실패합니다: `⚠ 부분 실패: alarm.trend.validate — ValueError: alarm.trend는 구간 분석이므로 config에 window가 필요합니다`
- **스냅샷 분석**에 `window`를 줘도 조용히 무시됩니다

**`cache_ttl`**은 같은 실행 컨텍스트로 다시 호출될 때 결과를 재사용합니다. 프로세스 메모리에만 있으므로 재시작하면 사라집니다.

---

### `subgraphs.<name>.nodes` — 슬롯 override

서브그래프의 4슬롯 중 일부만 다른 구현으로 갈아끼웁니다.

```json
{ "subgraphs": { "material.stock": { "nodes": { "process": "material.stock_gumi" } } } }
```

| 슬롯 키 | 대체되는 메서드 |
|---|---|
| `validate` | `validate_input` |
| `process` | `process` |
| `output` | `generate_output` |
| `error` | `handle_error` |

대상으로 **두 종류**를 쓸 수 있습니다.

| 대상 | 예 | 동작 |
|---|---|---|
| 공유 노드 부품 | `outputs.no_llm` | 함수를 원래 인스턴스에 바인딩 |
| 다른 서브그래프 | `material.stock_gumi` | 그 클래스로 인스턴스를 만들어 슬롯을 가져옴 |

후자가 **"상속은 코드에서, 선택은 config에서"**를 가능하게 합니다. 공장별로 로직 자체가 다를 때 쓰고, 그 클래스는 `@register(slot_only=True)`로 표시해야 다른 공장에서 고아로 잡히지 않습니다.

기본 제공 노드 부품:

| 이름 | 슬롯 | 동작 |
|---|---|---|
| `outputs.no_llm` | `output` | LLM 서술 없이 지표·판정만. 관측 기록은 그대로 보존 |

---

### `subgraphs.<name>.llm` — 모델 override

이 서브그래프만 다른 LLM을 쓰게 합니다. **기본 `llm` 블록 위에 deep merge**되므로 바꿀 키만 적으면 됩니다.

```json
{
  "llm": { "adapter": "chat_model", "model": "gpt-4o-mini" },
  "subgraphs": {
    "kpi.check": { "enabled": true, "llm": { "model": "gpt-4o" } }
  }
}
```

위에서 `kpi.check`는 `adapter`를 안 적었지만 `chat_model`을 물려받습니다.

기본은 `fake`로 두고 **한 서브그래프만 실제 LLM**에 붙이는 것도 됩니다 — 새 분석의 프롬프트를 다듬을 때 유용합니다.

override가 없는 서브그래프는 기본 LLM 인스턴스를 그대로 재사용합니다. 취합 노드는 항상 기본 LLM을 씁니다.

실제로 어떤 모델이 쓰였는지는 `LLMTrace`에 남습니다.

---

## 서브그래프별 필드

각 분석이 자기 스키마를 갖습니다. 여기 없는 키를 쓰면 부팅 시 `Extra inputs are not permitted`로 막힙니다.

### `health.connectivity` — 연결 상태 점검

스냅샷. Redis/MongoDB/Kafka/REST에 `ping()`을 보냅니다.

| 키 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `sources` | list[str] | `["redis","mongodb","kafka","rest"]` | 점검할 대상. 이름은 `Dependencies.health`의 키 |
| `latency_warn_ms` | float | `30.0` | 이 값을 넘으면 경고 |

연결 실패는 **심각**, 지연 초과는 **경고**로 판정합니다.

### `kafka.lag` — 컨슈머 지연

스냅샷. 오프셋 메타데이터만 읽고 메시지는 소비하지 않습니다.

| 키 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `groups` | list[str] | `[]` | 볼 컨슈머 그룹. **빈 리스트면 전체** |
| `warn_lag` | int | `5000` | 그룹 총 lag 경고 기준 |
| `critical_lag` | int | `30000` | 그룹 총 lag 심각 기준 |
| `skew_ratio` | float | `3.0` | 한 파티션 lag이 평균의 몇 배면 편중으로 볼지 |

`skew_ratio`는 총량이 정상이어도 **한 파티션만 밀리는 경우**(소비자 하나가 죽은 상황)를 잡습니다.

### `line.equipment` — 라인별 장비 상태

스냅샷. Redis에서 생산정보·라인정보·장비상태 세 갈래를 읽어 라인 단위로 합칩니다.

| 키 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `down_states` | list[str] | `["DOWN","ALARM"]` | 비가동으로 셀 상태값 |
| `warn_down_ratio` | float | `0.25` | 라인 장비 중 비가동 비율 경고 기준 |
| `critical_down_ratio` | float | `0.5` | 심각 기준 |
| `achievement_warn_pct` | float | `90.0` | 생산 달성률이 이 밑이면 경고 |

### `kpi.check` — KPI 점검

스냅샷. REST에서 KPI를 받아 목표 대비 이탈을 봅니다.

| 키 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `warn_gap_pct` | float | `-3.0` | 목표 대비 괴리율 경고 기준(음수) |
| `critical_gap_pct` | float | `-8.0` | 심각 기준(음수) |
| `use_llm_judge` | bool | `true` | 개별 임계치로 안 잡히는 **조합**을 LLM에게 판단시킬지 |

`use_llm_judge`를 켜면 LLM 호출이 한 번 늘고, LLM이 든 근거는 실제 입력 id와 대조되어 없는 것을 지어내면 폐기됩니다.

### `alarm.trend` — 알람 추세

**구간 분석**. `window` 필수. MongoDB에서 알람 이력을 읽어 `scen_id`별 추세를 봅니다.

| 키 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `window` | duration | — | **필수**. 예: `"P7D"` |
| `spike_ratio` | float | `1.5` | 후반 절반이 전반 절반의 몇 배면 증가로 볼지 |
| `min_total` | int | `5` | 이 건수 미만이면 추세로 판단하지 않음 |
| `top_n` | int | `5` | 리포트에 실을 상위 시나리오 수 |

`min_total`이 표본이 적을 때의 과잉 경보를 막습니다.

### `material.stock` — 자재 소진 예상

스냅샷. Redis에서 자재 재고를 읽어 소진까지 남은 시간을 계산합니다. [튜토리얼](tutorial.md)에서 처음부터 만들어보는 예시입니다.

| 키 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `warn_hours` | float | `8.0` | 소진까지 이 시간 이하면 경고 |
| `critical_hours` | float | `2.0` | 심각 기준 |
| `use_llm_judge` | bool | `false` | 조합 판단을 LLM에게 맡길지 |

### `material.stock_gumi` — 자재 소진 예상 (구미)

**슬롯 전용**(`@register(slot_only=True)`). config에 직접 등록하지 않고 `nodes.process` 대상으로만 씁니다. 구미의 자재 입고 주기(하루 두 번)를 반영해 "다음 입고까지 버티는가"로 판정합니다.

config 스키마는 `material.stock`과 동일하며, override된 서브그래프의 config를 그대로 씁니다.

---

## 어느 계층에 둘 것인가

| 성격 | 두는 곳 | 예 |
|---|---|---|
| 사업부 표준 | `gbm/{gbm}.json` | 기본 임계치, 켤 분석 목록, 리포트 양식 |
| 공장 공통 | `factories/{f}/common.json` | 그 공장의 컨슈머 그룹, 메일 수신자, 타임아웃 |
| 공장 + GBM 전용 | `factories/{f}/{gbm}.json` | 특정 조합만의 임계치, 로직 override |

**새 분석을 켤 때**는 보통 `gbm/{gbm}.json`에 `enabled`와 기본값을 두고, 공장별로 다른 값만 아래 계층에서 덮어씁니다.

---

## 전체 예시

```json
{
  "stores": { "timeout_sec": 5.0 },

  "ports": {
    "production": "redis",
    "line_info": "redis",
    "equipment_status": "redis",
    "material_stock": "redis",
    "consumer_lag": "kafka",
    "kpi": "rest",
    "alarms": "mongodb"
  },

  "llm": {
    "adapter": "fake",
    "provider": "openai_compatible",
    "model": "fake-local",
    "temperature": 0.0,
    "seed": "mx"
  },

  "subgraphs": {
    "health.connectivity": {
      "enabled": true,
      "sources": ["redis", "mongodb", "kafka", "rest"],
      "latency_warn_ms": 30.0
    },
    "kafka.lag": {
      "enabled": true,
      "groups": [],
      "warn_lag": 5000,
      "critical_lag": 30000,
      "skew_ratio": 3.0
    },
    "kpi.check": {
      "enabled": true,
      "warn_gap_pct": -3.0,
      "critical_gap_pct": -8.0,
      "use_llm_judge": true,
      "llm": { "model": "fake-judge" }
    },
    "alarm.trend": {
      "enabled": true,
      "window": "P7D",
      "spike_ratio": 1.5,
      "min_total": 5,
      "top_n": 5
    }
  },

  "report": { "template": "report.md" },

  "delivery": {
    "file": { "enabled": true },
    "mail": { "enabled": false, "recipients": [] }
  }
}
```

실제 파일은 [config/gbm/mx.json](../config/gbm/mx.json), [factories/gumi/common.json](../config/factories/gumi/common.json), [factories/gumi/mx.json](../config/factories/gumi/mx.json)을 보세요.
