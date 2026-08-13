# LLM 규약과 연결

LLM을 어디에 쓰고, 무엇을 강제하며, 실제 모델로 어떻게 바꾸는지.

- 설정 항목은 [config 레퍼런스의 `llm`](config-reference.md#llm)
- 전체 구조에서의 위치는 [아키텍처](architecture.md#llm-어댑터-llmpy)

---

## 어디에 쓰나

**취합 단계와 각 서브그래프의 `process` 양쪽**에서 씁니다. 다만 기준이 있습니다.

> **규칙으로 쓸 수 있으면 코드가 이깁니다.** 빠르고, 싸고, 항상 같은 답이 나오고, 테스트가 쉽습니다.

```python
if hours_left <= cfg.critical_hours:      # 이건 코드
    severity = Severity.CRITICAL
```

LLM은 규칙으로 표현할 수 없는 것만 맡습니다.

| 맡기는 것 | 맡기지 않는 것 |
|---|---|
| 여러 지표의 **조합**이 이상한가 | 임계치 비교 |
| 비정형 텍스트(로그·알람 설명)의 의미 | 산술 계산 |
| 사람이 읽을 **서술문** | 개수 세기 |

---

## 네 가지 강제

### 1. 숫자는 LLM이 다시 쓰지 않는다

"p95가 412ms"를 문장으로 옮기게 하면 언젠가 421ms로 씁니다.

숫자는 `Metric`에 담고 **템플릿이 State에서 직접 렌더링**합니다. LLM은 서술만 만듭니다.

### 2. 판정에는 근거를 강제한다

```python
class Judgement(BaseModel):
    subject: str
    severity: Severity
    reasoning: str
    evidence: list[str]      # 실제 입력 Record의 id
    confidence: float
```

`evidence`가 실제 id를 가리키므로 리포트의 어떤 문장이든 **원본 데이터까지 역추적**됩니다.

### 3. 근거를 코드로 검증한다 (환각 가드레일)

LLM이 든 `evidence` id가 실제 입력에 없으면 **그 판정을 폐기**합니다.

```
⚠ 가드레일: kpi.check: 근거 ['kpi:가동률::hallucinated']가 입력에 없어 판정을 폐기했습니다
```

이 검증은 **LLM 없이 코드로 돕니다.** 그래서 LLM이 아무리 그럴듯하게 지어내도 리포트까지 새지 않습니다.

폐기 기록은 State의 `guardrail_drops`에 쌓입니다 — 서브그래프마다 LLM이 다를 수 있어 어댑터에 두면 한곳에서 못 모읍니다.

### 4. 프롬프트·응답 원문을 남긴다

`LLMTrace`가 **변수 치환이 끝난 최종 프롬프트**와 raw 응답, model, temperature를 담아 State에 쌓입니다. 체크포인터에 함께 저장되므로 **별도 로깅 인프라가 없습니다.**

"프롬프트를 이렇게 보냈을 리가 없는데"는 실제로 자주 일어나는데, 렌더링 후 원문이 없으면 확인할 방법이 없습니다.

---

## process 안에서 LLM 부르기

```python
async def _judge_combinations(self, records: list) -> list[Judgement]:
    facts = "\n".join(
        f"- [{r.id}] {r.metadata['line']}: 재고 {r.record['stock_qty']:,}ea"
        for r in records
    )
    prompt = (
        "개별 임계치로는 잡히지 않는 위험 조합이 있는지 판단하세요. "
        "근거는 반드시 대괄호 안의 id로만 인용하세요.\n" + facts
    )
    return await self.deps.llm.judge(
        self.registry_name, prompt, [r.id for r in records]
    )
```

지켜야 할 것이 셋입니다.

1. **프롬프트에 id를 함께 싣는다** — `[stock:L1:MAT-A]` 형태. id 없이 사실만 주면 LLM은 근거를 지어낼 수밖에 없습니다
2. **사실은 `- `로 시작하는 줄로** — 지시문과 데이터를 구분하는 약속입니다
3. **`judge()`의 세 번째 인자가 허용된 id 목록** — 가드레일이 여기 없는 id를 걸러냅니다

기본은 **꺼두고** config로 켜는 걸 권합니다.

```python
class MaterialStockConfig(SubgraphConfig):
    use_llm_judge: bool = False
```

반환된 `Judgement`는 코드가 만든 것과 **똑같은 스키마**라 리포트에서 구분 없이 섞이고 근거 추적도 동일합니다.

---

## 전체 요약의 재료

취합 노드는 구조화된 `judgements`와 **사전 계산된 집계치**를 넘깁니다.

```python
counts = {
    "critical": sum(1 for j in judgements if j.severity is Severity.CRITICAL),
    ...
}
```

세부 요약 **문장**을 다시 요약하면 정보가 2단으로 손실되고, 무엇보다 "critical 몇 건"을 정확히 세지 못합니다. **개수는 코드가 세고 LLM은 해석과 우선순위만** 맡습니다.

---

## 구조

```
BaseLLMAdapter          ← 기록, replay, 가드레일 (공통)
├── FakeLLMAdapter      ← 규칙 기반. 외부 통신 없음. 기본값
└── ChatModelAdapter    ← ★ 실제 LLM과 통신하는 유일한 지점
```

하위 클래스는 **`_complete()`와 `_judge_raw()` 둘만** 구현합니다. 나머지(프롬프트 기록, replay, 환각 가드레일)는 공유하므로, **fake로 개발하다 실제로 바꿔도 관측·검증 동작이 그대로입니다.**

`ChatModelAdapter`에서 실제로 외부와 붙는 곳은 셋뿐입니다.

| 메서드 | 하는 일 |
|---|---|
| `_ensure_client()` | `init_chat_model()`로 클라이언트 생성 (지연 로드) |
| `_complete()` | 서술 — `ainvoke()` |
| `_judge_raw()` | 판정 — `with_structured_output()` |

---

## 실제 LLM으로 전환

**코드를 고칠 필요가 없습니다.** config 한 줄입니다.

```json
{ "llm": { "adapter": "chat_model", "provider": "openai_compatible",
           "model": "gpt-4o-mini", "temperature": 0.0 } }
```
```bash
# .env
LLM_BASE_URL=http://llm-gateway.internal/v1
LLM_API_KEY=...
```

| `adapter` | 동작 |
|---|---|
| `"fake"` (기본) | 규칙 기반 가짜 응답. LLM 없이 개발·테스트 |
| `"chat_model"` | 실제 LLM. 공급자 패키지가 이때만 로드됩니다 |

알 수 없는 값은 부팅에서 막힙니다.

```
✗ 알 수 없는 llm.adapter 'gpt'. 가능: fake, chat_model
```

### 공급자

`provider`는 `PROVIDER_ALIAS`를 거쳐 `init_chat_model`이 아는 이름으로 매핑됩니다.

| config 값 | 실제 |
|---|---|
| `openai_compatible` | `openai` |
| `azure_openai` | `azure_openai` |
| 그 외 | 그대로 전달 |

사내 게이트웨이가 OpenAI 호환이면 **`.env`의 `LLM_BASE_URL`만** 바꾸면 됩니다.

> `base_url`과 `api_key`가 config가 아닌 `.env`에 있는 이유는 **환경(dev/prod)별로 갈리는 축**이기 때문입니다. `model`·`temperature`는 GBM/FCT별로 다를 수 있어 config에 둡니다.

---

## 모델 바꾸기

config의 `llm.model` 한 줄입니다. **공장마다 다른 모델**도 3단 merge로 됩니다.

```json
// config/factories/asan/common.json
{ "llm": { "model": "gpt-4o", "temperature": 0.2 } }
```

```
mx/gumi  → model=gpt-4o-mini  temp=0.0   (llm.model ← gbm)
mx/asan  → model=gpt-4o       temp=0.2   (llm.model ← factory_common)
```

### 서브그래프별로 다른 모델

해당 블록에 `llm`을 넣습니다. 기본 설정 위에 **deep merge**되므로 바꿀 키만 적으면 됩니다.

```json
{
  "llm": { "adapter": "chat_model", "model": "gpt-4o-mini" },
  "subgraphs": {
    "kpi.check": { "enabled": true, "llm": { "model": "gpt-4o" } }
  }
}
```

위에서 `kpi.check`는 `adapter`를 안 적었지만 `chat_model`을 물려받습니다.

**기본은 fake로 두고 한 서브그래프만 실제 LLM**에 붙이는 것도 됩니다 — 새 분석의 프롬프트를 다듬을 때 유용합니다.

```json
{ "llm": { "adapter": "fake" },
  "subgraphs": { "kpi.check": { "llm": { "adapter": "chat_model", "model": "gpt-4o" } } } }
```

실제로 어떤 모델이 쓰였는지는 `LLMTrace`에 남습니다.

```
aggregate              fake-local
kpi.check              fake-judge    ←config의 llm override
line.equipment         fake-local
```

override가 없는 서브그래프는 **기본 인스턴스를 재사용**합니다. 취합 노드는 항상 기본 LLM을 씁니다.

---

## replay — LLM 고정하고 디버깅

저장된 응답을 재생해서 **LLM을 고정한 채 후속 로직만** 고쳐가며 확인합니다.

```bash
python -m src run ... --save-traces traces.json   # 1회차: 저장
python -m src run ... --replay traces.json        # 2회차: 재생
```

```
  replay: 8건의 저장된 응답을 재생합니다
LLM 호출 8건 (재생 8건) · 서브그래프 6건
```

`narrate`와 `judge` **둘 다** 재생됩니다. judge의 trace에는 판정이 JSON으로 남아 있어 복원됩니다 — "3건의 판정" 같은 요약만 남기면 복원할 수 없습니다.

"리포트가 이상한데 **LLM 판단이 틀린 건지 취합 로직이 틀린 건지**"를 분리하는 용도입니다.

---

## 실제 LLM에서 확인할 것

| | |
|---|---|
| **structured output** 지원 모델인가 | `judge()`가 `with_structured_output`을 씁니다. 지원 안 하면 판정 경로가 실패 |
| 가드레일 폐기가 잦은가 | 프롬프트에 id를 충분히 싣지 않았다는 신호 |
| 첫 실행에 `--save-traces` | 프롬프트를 실제로 어떻게 보냈는지 확인 |
| 자격증명 누락 | 그 서브그래프만 부분 실패로 격리됩니다 |

---

## 한계

`judge()`의 재시도가 아직 없습니다. 근거 검증에 실패하면 **재시도 없이 바로 폐기**합니다. 실제 LLM에서 폐기가 잦다면 1회 재시도를 넣는 게 맞습니다 — [llm.py](../src/infrastructure/llm.py)의 `_enforce_evidence`에 자리를 표시해뒀습니다.
