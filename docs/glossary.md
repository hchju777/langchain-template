# 용어집과 판단 가이드

이 저장소에서 반복해서 나오는 용어와, 새 코드를 쓸 때 반복해서 부딪히는 판단을 모았습니다.

- 전체 구조는 [README](../README.md)
- 설정 항목은 [config 레퍼런스](config-reference.md)
- 처음이면 [튜토리얼](tutorial.md)

---

## 용어

### 배포 좌표

| 용어 | 뜻 |
|---|---|
| **GBM** | 사업부. 예: `mx` |
| **FCT / factory** | 공장. 예: `gumi` |
| **as_of** | 분석 기준 시각. **밖에서 주입**되며 노드 안에서 `now()`를 부르지 않습니다 |
| **thread_id** | 실행 하나의 식별자. `{gbm}:{factory}:{as_of}` |

`as_of`가 이 저장소에서 가장 많이 되돌아오는 개념입니다. 재실행 재현성, Time Travel, 멱등 발송, 중복 실행 락이 전부 여기에 걸려 있습니다.

### 그래프

| 용어 | 뜻 |
|---|---|
| **서브그래프** | 분석 하나. config로 켜고 끄는 단위이며 리포트 섹션 하나가 됩니다 |
| **슬롯** | 서브그래프의 4단계 — `validate` / `process` / `output` / `error` |
| **노드 부품** | 여러 서브그래프가 공유하는 슬롯 구현. 예: `outputs.no_llm` |
| **슬롯 전용** | `@register(slot_only=True)`. 독립 실행하지 않고 override 대상으로만 쓰이는 서브그래프 |
| **degraded 섹션** | 실패한 서브그래프가 남기는 섹션. 리포트는 계속 나옵니다 |
| **리듀서** | 병렬 노드의 State를 합치는 규칙. `domain/reducers.py` 한곳에 모읍니다 |

### 데이터

| 용어 | 뜻 |
|---|---|
| **Record** | 수집 경계를 넘어온 데이터. `{id, metadata, record}` |
| **kind** | 어떤 데이터를 원하는지. 예: `material_stock`. **어느 저장소인지는 모릅니다** |
| **포트(Port)** | domain이 선언하는 계약(`Protocol`). 구현은 바깥 계층 |
| **어댑터** | 포트 구현체. Redis·Mongo·Kafka·REST·LLM·메일·렌더러 |
| **라우터** | kind → 어댑터. config의 `ports`가 매핑을 정합니다 |

### 판정과 리포트

| 용어 | 뜻 |
|---|---|
| **Judgement** | 판정. `verdict` + `reasoning` + **`evidence`(실제 Record id)** |
| **Metric** | 리포트 표에 그대로 찍히는 숫자. LLM이 다시 쓰지 않습니다 |
| **가드레일** | LLM이 든 근거가 실제 입력에 있는지 코드로 대조. 없으면 판정 폐기 |
| **LLMTrace** | 변수 치환이 끝난 최종 프롬프트 + raw 응답. State에 쌓입니다 |
| **replay** | 저장된 LLM 응답 재생. LLM을 고정한 채 후속 로직만 디버깅 |

### 설정

| 용어 | 뜻 |
|---|---|
| **3단 병합** | `gbm` → `factory/common` → `factory/{gbm}`. 뒤가 앞을 덮어씀 |
| **출처 추적** | 각 값이 어느 계층에서 왔는지. `config show`가 보여줍니다 |
| **부팅 검증** | config와 코드의 어긋남을 시작 시점에 전부 모아 보고 |
| **Composition Root** | config를 보는 유일한 곳 (`graph/builder.py`) |

---

## 판단 가이드

### 새 서브그래프를 만들까, 기존 것을 고칠까

**새로 만드세요** — 리포트에 **별도 섹션**으로 나와야 한다면. 섹션 단위가 곧 서브그래프 단위입니다.

**기존 것을 고치세요** — 같은 섹션 안에 지표나 판정이 하나 더 붙는 것이라면.

애매하면 이걸 물어보세요: **"이것만 따로 끄고 싶을 때가 있을까?"** 있다면 별도 서브그래프입니다. config의 `enabled`가 그 단위로 동작하니까요.

---

### 코드로 판정할까, LLM에게 맡길까

**규칙으로 쓸 수 있으면 코드가 이깁니다.** 빠르고, 싸고, 항상 같은 답이 나오고, 테스트가 쉽습니다.

```python
if hours_left <= cfg.critical_hours:      # 이건 코드
    severity = Severity.CRITICAL
```

**LLM은 규칙으로 표현할 수 없는 것만** 맡습니다.

- 여러 지표의 **조합**이 이상한가 (개별 임계치로는 안 잡힘)
- 비정형 텍스트(로그 메시지, 알람 설명)의 의미
- 사람이 읽을 **서술문** 생성

특히 **숫자를 LLM에게 다시 쓰게 하지 마세요.** "p95가 412ms"를 문장으로 옮기게 하면 언젠가 421ms로 씁니다. 숫자는 `Metric`에 담아 템플릿이 직접 렌더링합니다.

LLM을 쓴다면 반드시 `judge()`에 **허용된 id 목록**을 넘기세요. 가드레일이 없는 id를 걸러냅니다.

---

### 상속으로 만들까, config로 조정할까

| 상황 | 방법 |
|---|---|
| **임계치만** 다르다 | config 값 (`warn_hours: 12.0`) |
| **로직 자체**가 다르다 | 상속 + 슬롯 override |
| 서술만 빼고 싶다 | 노드 부품 (`outputs.no_llm`) |

**상속은 코드에서, 선택은 config에서.**

```python
@register(slot_only=True)              # ← 다른 공장에서 고아로 안 잡히게
class GumiMaterialStock(MaterialStock):
    async def process(self, state): ...
```
```json
{ "subgraphs": { "material.stock": { "nodes": { "process": "material.stock_gumi" } } } }
```

> **함정**: 상속 클래스가 부모의 config 키를 **다른 의미로** 쓰기 쉽습니다. 실제로 `material.stock_gumi`가 `critical_hours`를 "소진까지 시간"이 아니라 "입고 후 여유"로 해석합니다. 의미가 달라지면 **config 모델도 따로 만드는 게** 낫습니다.

---

### config에 넣을까, `.env`에 넣을까

두 가지만 물어보면 됩니다.

1. **git에 커밋해도 되는가?** → 아니면 `.env`
2. **GBM/FCT가 아니라 환경(dev/prod)에 따라 바뀌는가?** → 그러면 `.env`

같은 기능이 양쪽으로 갈리는 게 정상입니다.

| | config JSON | `.env` |
|---|---|---|
| LLM | `model`, `temperature` | `LLM_BASE_URL`, `LLM_API_KEY` |
| 메일 | `recipients` | `SMTP_HOST`, `SMTP_PWD` |
| 저장소 | `stores.timeout_sec` | `MONGODB_URI` |

---

### 상수를 어디에 둘까

| 범위 | 위치 |
|---|---|
| 여러 모듈이 쓴다 | `src/constants.py` |
| 한 모듈 안에서만 | 그 파일 상단 |
| GBM/FCT마다 달라야 한다 | **상수가 아니라 config** |

매직값을 코드에 직접 쓰지 않는 게 원칙이지만, **config로 뺄지 상수로 둘지**는 "공장마다 다를 수 있나"로 정합니다.

---

### 부팅에서 막을까, 실행 중에 처리할까

**config로 알 수 있는 것은 부팅에서 막습니다.** 새벽 배치에서 발견되면 늦습니다.

```
✗ 설정 검증에 실패했습니다 (2건):
  - 'subgraphs.kafka.lag.warn_lagg': Extra inputs are not permitted
  - 서브그래프 'material.stock'이 요청하는 'material_stock'을(를) ... 'ports'에 없습니다
```

**실행해봐야 아는 것은 부분 실패로 격리합니다.** 외부 시스템 장애, 데이터 이상 같은 것들요. 한 분석이 죽어도 리포트는 나옵니다.

| 부팅에서 막는 것 | 실행 중 격리하는 것 |
|---|---|
| config 키 오타·타입 오류 | 저장소 연결 실패·타임아웃 |
| 없는 서브그래프·노드 이름 | 예상과 다른 데이터 모양 |
| 매핑 안 된 kind | LLM 응답 이상 |
| 없는 템플릿 파일 | |

---

### 테스트를 어디에 쓸까

**대부분은 `process`만 직접 부르면 됩니다.** 가짜 포트 하나면 충분하고 그래프도 config 파일도 필요 없습니다.

```python
sub = MySubgraph(MyConfig(enabled=True), FakeDeps(data=FakePort([...])))
out = asyncio.run(sub.process(SubgraphState(ctx=CTX, scoped=CTX)))
```

**그래프 전체 테스트**는 여러 서브그래프가 얽히는 동작에만 씁니다 — 부분 실패 격리, 취합, 발송 멱등성 같은 것들.

경계값(임계치에 딱 걸리는 값), 빈 입력, 0으로 나누기를 꼭 넣으세요. 셋 다 실제로 버그가 나온 자리입니다.

자세한 건 [tests/README.md](../tests/README.md)를 보세요.

---

## 자주 하는 실수

| 증상 | 원인 |
|---|---|
| 임계치를 바꿨는데 안 먹는다 | 키 오타 — 이제 부팅에서 잡힙니다 |
| config를 고쳤는데 반영이 안 된다 | 다른 계층이 덮어쓰고 있음. `config show`로 출처 확인 |
| 스니펫을 붙여넣었는데 아무 일도 안 일어난다 | `"subgraphs"` 래퍼 누락 |
| 월요일에 돌게 했는데 화요일에 돈다 | cron 요일 숫자. **이름을 쓰세요** (`mon`) |
| 재실행했는데 결과가 다르다 | 노드 안에서 `datetime.now()` 호출 |
| 슬롯을 갈아끼웠더니 trace가 사라졌다 | 부품이 `drain_traces()`를 안 넘김 |
| 새 분석이 "고아"라고 부팅이 막힌다 | config에 등록 안 함, 또는 `slot_only=True` 누락 |
