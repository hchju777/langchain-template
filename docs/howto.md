# 작업별 가이드 (How-to)

**"무엇을 하고 싶은가"로 찾는 색인입니다.** 다른 문서들은 개념·설정키·시간 순서로 조직되어 있어서, 하려는 일이 정해졌을 때 어디를 봐야 할지 바로 나오지 않습니다. 이 문서가 그 자리를 채웁니다.

대부분은 **이미 있는 문서로 안내**하고, 어디에도 없던 것만 여기서 직접 설명합니다.

---

## 한눈에

| 하고 싶은 것 | 어디로 | 난이도 |
|---|---|---|
| [새 분석 추가](#새-분석-추가) | [튜토리얼](tutorial.md) 전체 | ●○○ |
| [기능 껐다 켜기](#기능-껐다-켜기) | config 한 줄 | ●○○ |
| [공장마다 다른 임계치](#공장마다-다른-임계치) | [튜토리얼 5단계](tutorial.md) | ●○○ |
| [공장마다 다른 로직](#공장마다-다른-로직) | [README 핵심개념 6](../README.md#6-gbmfct별-분기는-config로만) | ●●○ |
| [LLM 서술만 끄기](#llm-서술만-끄기) | config 한 줄 | ●○○ |
| [리포트 양식 교체](#리포트-양식-교체) | config 한 줄 | ●○○ |
| [새 템플릿 파일 작성](#새-템플릿-파일-작성) | [README 리포트 템플릿](../README.md#리포트-템플릿) + 아래 변수표 | ●●○ |
| [실제 LLM으로 전환](#실제-llm으로-전환) | [README LLM 연결](../README.md#llm-연결과-모델-교체) | ●○○ |
| [서브그래프마다 다른 모델](#서브그래프마다-다른-모델) | [README](../README.md#서브그래프별로-다른-모델) | ●○○ |
| [실제 DB로 전환](#실제-db로-전환) | [README 실제 연결로 전환](../README.md#실제-연결로-전환) + 아래 보충 | ●●● |
| [새 데이터 kind 추가](#새-데이터-kind-추가) | [튜토리얼 1·3단계](tutorial.md) + 아래 보충 | ●●○ |
| [구간(historical) 분석](#구간historical-분석) | [튜토리얼 "구간 분석으로 만들기"](tutorial.md) | ●●○ |
| [메일 발송 설정](#메일-발송-설정) | [config 레퍼런스 `delivery`](config-reference.md) + 아래 검증법 | ●●○ |
| [스케줄링(cron)](#스케줄링cron) | [config 레퍼런스 `schedule`](config-reference.md) | ●●○ |
| [디버깅 — 값 출처 추적](#디버깅--값-출처-추적) | `config show` | ●○○ |
| [디버깅 — 진행 상황 보기](#디버깅--진행-상황-보기) | 아래 | ●○○ |
| [디버깅 — LLM 고정하고 재현](#디버깅--llm-고정하고-재현) | **아래 (여기에만 있음)** | ●●○ |
| [Time Travel·fork](#time-travelfork) | **아래 (여기에만 있음)** | ●●● |
| [테스트 작성](#테스트-작성) | [tests/README](../tests/README.md) | ●●○ |
| [부팅 에러 메시지 해석](#부팅-에러-메시지-해석) | [튜토리얼 "흔한 실수"](tutorial.md) | ●○○ |

> LangGraph 프레임워크 자체(State·리듀서·`Command`·체크포인터가 **왜** 그렇게 생겼는지)가 궁금하면 [ref/](../ref/README.md)를 보세요. 이 문서는 **이 템플릿을 쓰는 법**만 다룹니다.

---

## 자주 하는 일

### 새 분석 추가

**파일 1개 + config 1줄.** 보통 `process`만 구현하면 됩니다.

→ **[튜토리얼](tutorial.md)을 그대로 따라가세요.** README의 "새 분석 추가하기"는 요약본이라, 실제로 막히는 지점(데이터를 어디서 가져올지 `ports`에 알려주는 것)이 빠져 있습니다.

작업 후 확인:

```bash
python -m src registry                                  # 등록됐나
python -m src config show --gbm mx --factory gumi       # config가 읽히나
python -m src run --gbm mx --factory gumi               # 섹션이 나오나
```

### 기능 껐다 켜기

```json
{ "subgraphs": { "kafka.lag": { "enabled": false } } }
```

끈 서브그래프는 그래프에 아예 추가되지 않습니다(빈 섹션이 나오는 게 아니라 섹션 자체가 없음).

### 공장마다 다른 임계치

3단 merge에서 아래 계층이 위를 덮어씁니다. **바꿀 키만** 적으면 됩니다.

```json
// config/factories/gumi/mx.json
{ "subgraphs": { "kafka.lag": { "critical_lag": 5000 } } }
```

→ [튜토리얼 5단계](tutorial.md), [config 레퍼런스 "어느 계층에 둘 것인가"](config-reference.md)

### 공장마다 다른 로직

**상속은 코드에서, 선택은 config에서.**

```python
# subgraphs/material/stock_gumi.py   → "material.stock_gumi"
@register(slot_only=True)          # config에 직접 등록되지 않는다는 선언
class GumiMaterialStock(MaterialStock):
    async def process(self, state): ...
```
```json
{ "subgraphs": { "material.stock": { "nodes": { "process": "material.stock_gumi" } } } }
```

`slot_only=True`가 없으면 다른 공장에서 "고아" 검증에 걸립니다.

→ [README 핵심개념 6](../README.md#6-gbmfct별-분기는-config로만)

### LLM 서술만 끄기

```json
{ "subgraphs": { "kpi.check": { "nodes": { "output": "outputs.no_llm" } } } }
```

지표(표)는 그대로 나오고 LLM 서술 문장만 빠집니다. 슬롯 이름은 `validate` / `process` / `output` / `error`입니다.

### 리포트 양식 교체

```json
{ "report": { "template": "report_brief.md" } }
```

템플릿 파일이 없으면 **부팅 시** 터집니다(8시 배치가 아니라).

### 새 템플릿 파일 작성

`src/presentation/templates/`에 파일을 놓고 config에서 이름을 부르면 끝입니다. 블록 문법과 변수 목록은 [README 리포트 템플릿](../README.md#리포트-템플릿)에 있습니다.

**보충 — 각 변수가 실제로 무엇으로 치환되는가.** 기존 템플릿을 역공학하지 않아도 되도록 정리합니다.

| 변수 | 치환 결과 | 예 |
|---|---|---|
| `${mark}` | 심각도 이모지 | 🟢 정상 · 🟡 경고 · 🔴 심각 |
| `${severity}` | 심각도 한글 라벨 | `경고` |
| `${degraded_mark}` | 부분 실패일 때만 값이 있음 | ` _(부분 실패)_` (아니면 빈 문자열) |
| `${narrative}` | LLM 서술 **원문** (줄바꿈 유지) | 여러 줄 |
| `${narrative_inline}` | 줄바꿈을 공백으로 접은 한 줄. 비어 있으면 `특이사항 없음` | 한 줄 리스트형 템플릿용 |
| `${metrics}` | **완성된 md 표**(헤더 포함). 지표가 없으면 빈 문자열 | `\| 지표 \| 값 \| 비고 \|` + 행들 |
| `${judgements}` | `judgements` 블록을 채운 결과. 블록을 정의 안 했으면 빈 문자열 | |
| `${confidence}` | 백분율 문자열 | `100%` |
| `${evidence}` | 근거 id를 `, `로 이어붙임. 없으면 `없음` | `stock:L2:MAT-A` |

세 가지 규칙만 기억하면 됩니다.

- **없는 변수는 빈 문자열**이 됩니다(`safe_substitute`). 오타가 나도 죽지 않으므로 결과물을 눈으로 확인하세요.
- **`${metrics}`는 표 전체**입니다. 앞에 `| 지표 |` 같은 헤더를 직접 쓰면 중복됩니다.
- **블록을 정의하지 않으면 그 부분이 통째로 생략**됩니다. `report_brief.md`가 `judgements`/`judgement_item`을 비워 판정 근거를 빼는 방식입니다.

숫자는 항상 State에서 직접 찍히고 **LLM이 다시 쓰지 않습니다** — 그래서 표의 값은 서술과 어긋날 수 없습니다.

---

## 설정·연결

### 실제 LLM으로 전환

코드 수정 없이 config 한 줄입니다.

```json
{ "llm": { "adapter": "chat_model", "model": "gpt-4o-mini" } }
```
```bash
# .env
LLM_BASE_URL=http://llm-gateway.internal/v1
LLM_API_KEY=...
```

사내 게이트웨이가 OpenAI 호환이면 `.env`의 `LLM_BASE_URL` 교체만으로 끝납니다.

→ [README LLM 연결과 모델 교체](../README.md#llm-연결과-모델-교체)

### 서브그래프마다 다른 모델

해당 블록에 `llm`을 넣으면 기본 설정 위에 deep merge됩니다.

```json
{ "llm": { "adapter": "fake" },
  "subgraphs": { "kpi.check": { "llm": { "adapter": "chat_model", "model": "gpt-4o" } } } }
```

**기본은 fake로 두고 한 서브그래프만 실제 LLM**을 붙이는 게 새 프롬프트를 다듬을 때 유용합니다.

→ [README 서브그래프별로 다른 모델](../README.md#서브그래프별로-다른-모델)

### 실제 DB로 전환

`src/infrastructure/stores.py`의 각 어댑터에 실제 호출이 주석으로 있습니다. `fake_data` 호출을 그것으로 바꾸고 `fake_data.py`를 지우면 됩니다.

→ [README 실제 연결로 전환](../README.md#실제-연결로-전환)

**보충 — 문서에 없던 것들:**

| 항목 | 내용 |
|---|---|
| 클라이언트 생성 | `BaseAdapter`가 지연 생성·수명 관리를 담당합니다. `_call`(타임아웃·재시도)과 `_to_domain` 호출 위치는 **그대로 두세요** |
| `.env`가 비어 있으면 | 지금은 스텁이라 그냥 돕니다. 실제 어댑터로 바꾸면 **연결 시도 시점**에 실패합니다(부팅 시가 아님) |
| `ping()` | `health.connectivity`가 이걸 부릅니다. 어댑터를 실제 구현으로 바꿀 때 `ping()`도 같이 구현해야 헬스체크가 의미를 갖습니다 |
| 어댑터 정리 | `Dependencies.close()`가 `adapters` 리스트를 순회합니다. 새 어댑터는 여기 등록되어야 연결이 닫힙니다 |

### 새 데이터 kind 추가

1. 서브그래프에서 `FetchSpec(kind="my_kind")`로 요청
2. config `ports`에 `"my_kind": "redis"` 처럼 어댑터를 지정
3. **해당 어댑터가 그 kind를 지원한다고 선언**해야 합니다 — 어댑터의 `supported_kinds`에 추가하세요. 빠뜨리면 부팅 검증에서 걸립니다

→ [튜토리얼 1·3단계](tutorial.md), [config 레퍼런스 `ports`](config-reference.md)

### 구간(historical) 분석

`context_type = HistoricalContext`로 두고 config에 `window`를 ISO duration으로 씁니다.

```json
{ "subgraphs": { "alarm.trend": { "window": "P7D" } } }
```

`validate_input`이 `as_of` 기준으로 절대 시각 쌍(`start_dt`/`end_dt`)을 만들어줍니다. **노드 안에서 `datetime.now()`를 부르면 안 됩니다** — 재개·Time Travel·멱등성이 전부 깨집니다.

→ [튜토리얼 "구간 분석으로 만들기"](tutorial.md)

### 메일 발송 설정

수신자는 config, 서버는 `.env`로 갈립니다.

```json
{ "delivery": { "mail": { "enabled": true, "recipients": ["ops@example.com"] } } }
```

→ [config 레퍼런스 `delivery`](config-reference.md)

**보충 — 실제 SMTP 없이 확인하는 법:** 지금은 발송이 스텁이라 실행하면 실제로 보내지 않고 목적지만 출력합니다. 배선이 맞는지는 이 줄로 확인하면 됩니다.

```
✓ 발송[mail] → mail://gumi-ops@example.com?subject=[구미 운영리포트] mx_gumi_20260813T0800
```

수신자·제목 접두사가 의도대로 병합됐는지는 `config show`의 `delivery.mail.recipients` 출처로 확인하세요.

### 스케줄링(cron)

**구현되어 있고 동작합니다.**

```bash
python -m src scheduler --gbm mx --factory gumi          # 상주
python -m src scheduler --gbm mx --factory gumi --once   # 배선 확인용 1회 실행
```

```json
{ "schedule": { "cron": "0 8 * * *", "timezone": "Asia/Seoul" } }
```

실행하면 다음 실행 시각을 미리 보여줍니다.

```
cron '0 8 * * *' (Asia/Seoul)
  다음 실행: 2026-08-14 08:00:00 KST
  다음 실행: 2026-08-15 08:00:00 KST
  다음 실행: 2026-08-16 08:00:00 KST
```

> ⚠️ **요일 숫자 함정**: APScheduler는 `0=월요일`이라 표준 cron(`0=일요일`)과 하루씩 어긋납니다. 숫자 대신 `mon`/`tue` 같은 이름을 쓰세요 — 숫자를 쓰면 경고가 뜹니다.

중복 실행은 `RunLock`이 `{gbm, factory, as_of}` 기준으로 막습니다(CLI·스케줄러 양쪽 적용).

→ [config 레퍼런스 `schedule`](config-reference.md)

---

## 디버깅

### 디버깅 — 값 출처 추적

3단 merge에서 "이 값이 왜 이래?"는 반드시 나오는 질문입니다.

```bash
python -m src config show --gbm mx --factory gumi
```

```
  ○ gbm              config/gbm/mx.json          ← ○는 파일이 실제로 읽혔다는 뜻
  × factory_common   ...                          ← ×면 그 파일이 없는 것

subgraphs.kafka.lag.critical_lag       ← factory_gbm
subgraphs.kafka.lag.warn_lag           ← gbm
```

없는 factory를 지정해도 **에러가 나지 않고** gbm 계층만 적용됩니다. 의도한 파일이 읽혔는지 `○`/`×`로 확인하세요.

### 디버깅 — 진행 상황 보기

```bash
python -m src run --gbm mx --factory gumi --stream
```

노드가 끝날 때마다 이름을 찍습니다. 어느 서브그래프에서 오래 걸리는지, 어디서 멈췄는지 볼 때 씁니다.

```
  · health.connectivity
  · kafka.lag
  · aggregate
```

### 디버깅 — LLM 고정하고 재현

**LLM 응답을 저장해두고 재생하면, LLM을 고정한 채 후속 로직만 고쳐가며 디버깅할 수 있습니다.**

```bash
# 1. 저장
python -m src run --gbm mx --factory gumi --as-of 2026-08-13T08:00 \
    --save-traces traces.json

# 2. 재생 — LLM을 부르지 않고 저장된 응답을 씀
python -m src run --gbm mx --factory gumi --as-of 2026-08-13T08:00 \
    --replay traces.json
```

성공하면 요약에 재생 건수가 붙습니다.

```
LLM 호출 8건 (재생 8건) · 서브그래프 6건
```

`traces.json`은 이런 리스트입니다.

```json
[{ "node": "alarm.trend", "prompt": "...", "response": "...",
   "model": "fake-local", "temperature": 0.0, "replayed": false }]
```

> ⚠️ **가장 흔한 실수 — `as_of`를 바꾸면 재생이 안 됩니다.** 매칭 키가 `노드명 + 프롬프트 전문`이라, 프롬프트가 한 글자라도 다르면 그냥 LLM을 새로 부릅니다(에러는 안 남). `as_of`가 바뀌면 데이터가 바뀌고 → 프롬프트가 바뀌므로 **저장할 때와 같은 `as_of`를 쓰세요.**
>
> 같은 이유로 **프롬프트 템플릿을 고치면 그 노드는 재생되지 않습니다.** 이건 의도된 동작입니다 — 프롬프트를 바꿨으면 응답도 새로 받아야 하니까요.
>
> 재생 건수가 예상보다 적으면 프롬프트가 달라진 것입니다. `--replay`는 조용히 실패하므로 **요약의 `(재생 N건)`을 반드시 확인하세요.**

`--replay`의 인자는 **파일 경로**입니다(thread_id가 아닙니다).

### Time Travel·fork

체크포인터 덕분에 **과거 시점의 State를 보거나, 값을 바꿔 다른 경로로 다시 돌릴 수 있습니다.** 설계 문서와 config 레퍼런스는 "된다"고만 하고 방법이 없어서, 여기에 실제로 동작하는 절차를 둡니다.

먼저 체크포인트 목록을 봅니다.

```bash
python -m src run --gbm mx --factory gumi --as-of 2026-08-13T09:00 --show-checkpoints
```

```
체크포인트 (thread_id=mx:gumi:20260813T0900)
  step -1  다음: __start__
  step  0  다음: health.connectivity, kafka.lag, ... (6개 병렬)
  step  1  다음: aggregate
  step  2  다음: render
  step  3  다음: deliver
  step  4  다음: (완료)
```

세부 조작은 CLI에 없으므로 스크립트로 합니다. 아래는 **실제로 동작을 확인한 코드**입니다.

```python
import asyncio
from datetime import datetime

from src.application.usecase import run_report
from src.config.env import EnvConfig


async def main() -> None:
    run = await run_report(
        gbm="mx", factory="gumi",
        as_of=datetime.fromisoformat("2026-08-13T10:00"),
        env=EnvConfig(),
    )
    graph, cfg = run.graph, run.run_config

    # 1. 이력 조회 (최신순)
    history = [s async for s in graph.aget_state_history(cfg)]
    for s in history:
        print(s.metadata.get("step"), s.next)

    # 2. 특정 시점 State 보기 — aggregate 직전
    target = next(s for s in history if s.next == ("aggregate",))
    snap = await graph.aget_state(target.config)
    print(len(snap.values["sections"]), "개 섹션")

    # 3. 값을 바꿔 분기(fork)
    forked = await graph.aupdate_state(
        target.config,
        {"sections": snap.values["sections"][:2]},
        as_node="material.stock",
    )

    # 4. 분기 지점부터 재실행
    result = await graph.ainvoke(None, forked)
    print(len(result["rendered"]), "자 리포트")


asyncio.run(main())
```

프로젝트 루트에서 `python 스크립트.py`로 실행합니다(`src`를 import하므로).

**세 가지 함정** — 전부 실제로 밟아본 것들입니다.

| 증상 | 원인 | 해결 |
|---|---|---|
| `KeyError: 'checkpoint_ns'` | config를 손으로 만들어서 `checkpoint_ns`가 빠짐 | 이력 항목의 **`.config`를 그대로 넘기세요**. `{"configurable": {...}}`를 직접 조립하지 마세요 |
| `InvalidUpdateError: Ambiguous update, specify as_node` | 6개 서브그래프가 **병렬**로 같은 스텝에 쓰기 때문에 어느 노드의 업데이트인지 추론 불가 | `as_node="material.stock"`처럼 **명시**하세요 |
| 섹션을 2개로 줄였는데 결과는 6개 | `sections`의 리듀서가 `merge_sections`(**key 기준 dedupe, 나중 것이 승리**)라 리스트를 통째로 교체하지 않음 | 항목을 **바꾸는** 건 되지만 **줄이는** 건 안 됩니다. 줄이려면 리듀서를 우회해야 합니다 |

마지막 항목이 특히 중요합니다 — fork는 "값을 덮어쓴다"가 아니라 **"리듀서를 한 번 더 통과시킨다"**입니다. 어떤 키를 어떻게 바꿀 수 있는지는 [state.py](../src/application/graph/state.py)의 `Annotated[...]` 리듀서를 보고 판단하세요.

> `checkpoint.backend`가 `"memory"`면 **프로세스 안에서만** 동작합니다. 위 스크립트처럼 한 프로세스에서 실행→조회→fork까지 하는 건 되지만, 어제 실행분을 오늘 불러오려면 영속 백엔드가 필요합니다.

### 테스트 작성

```bash
python -m pytest tests/ -q
```

새 분석을 만들었으면 `process`를 직접 호출하는 테스트를 추가합니다. 외부 의존이 없어 그냥 돕니다.

→ [tests/README](../tests/README.md)

### 부팅 에러 메시지 해석

부팅 검증은 **모든 문제를 모아서 한 번에** 보고합니다.

| 메시지 | 원인 |
|---|---|
| `...이 레지스트리에 없습니다` | config의 서브그래프 이름 오타 |
| `...등록되었지만 어느 config에도 없습니다 (고아)` | 코드는 있는데 config에 안 씀. 슬롯 부품이면 `slot_only=True` |
| `Input should be a valid integer` | config 값 타입이 스키마와 다름 |
| `...가 가리키는 ...이 없습니다` | `nodes` 슬롯 override 대상 이름 오타 |

→ [튜토리얼 "흔한 실수와 그때 나오는 메시지"](tutorial.md)

---

## 더 알아보기

| | |
|---|---|
| [README](../README.md) | 구조·계층 경계·설계 원칙 |
| [설계 문서](superpowers/specs/2026-08-13-langgraph-report-template-design.md) | 왜 이렇게 만들었나, 버린 대안들 |
| [ref/](../ref/README.md) | LangGraph 프레임워크 자체 |
