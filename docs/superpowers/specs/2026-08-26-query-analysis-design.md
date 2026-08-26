# 질의 분석과 참고 문서 반영 — 설계

작성일: 2026-08-26
대상: langgraph-template
선행 문서: [LLM 자율성 확대 방향 기술 검토](2026-08-26-llm-autonomy-review.md) 8-2절

---

## 1. 목적과 범위

사용자의 자연어 질의를 받아 **리포트의 범위와 서술 초점을 조정**하고, 운영 기준 문서를 취합 단계에 반영한다.

### 포함

- `START`와 서브그래프 사이에 질의 분석 노드(`analyze_query`) 추가
- 질의에 따라 실행할 서브그래프를 선택하는 조건부 fan-out
- 선택 결과에 대한 결정론적 가드레일
- 질의를 실행 식별자(`thread_id`, 발송 멱등키)에 반영
- 참고 문서를 공급하는 `ReferencePort`와 정적 어댑터
- 취합 서술의 인용 사후 검사
- 질의 실행 시 브로드캐스트 채널 발송 제외

### 제외

- `process` 내부 구조 변경 — 별도 프로젝트(B)
- RAG / 벡터 검색 — `ReferencePort`의 어댑터 교체 지점만 남긴다
- 대화형 온디맨드 질의 기능 — 별도 진입점이며 이 스펙의 대상이 아니다
- 질의에 의한 서브그래프 config 값(임계치·window) 변경 — "config는 조립 시점에만" 규약을 깬다

### 성공 기준

1. 질의 없이 실행하면 **현재와 완전히 동일하게** 동작한다. `thread_id` 문자열, 실행되는 서브그래프, 발송 채널이 모두 그대로다.
2. 질의를 주면 관련 서브그래프만 실행되고, 서술이 질의의 초점을 반영한다.
3. 같은 `(as_of, 질의)`로 재실행하면 같은 스레드에 이어붙고 중복 발송이 없다.
4. LLM이 존재하지 않는 서브그래프 이름을 제시해도 리포트가 정상 생성된다.

---

## 2. 배경

두 가지 제약이 이 변경의 출발점이다.

**질의를 받을 입구가 없다.** `BaseContext`는 `as_of`·`gbm`·`factory`만 담고, 실행되는 서브그래프는 config의 `enabled`로 고정된다.

**취합 단계가 배경 지식을 모른다.** `aggregate`는 각 서브그래프의 판정 결과만 본다. "오늘 A라인은 정기점검 중"이라는 사실을 알면 장비 비가동을 다르게 해석해야 하지만, 그 정보가 들어올 통로가 없다.

---

## 3. 아키텍처 변경

```
현재:  START ──fan-out──→ [활성 서브그래프 전체] ──→ aggregate ──→ render ──→ deliver

변경:  START ──→ analyze_query ──조건부 fan-out──→ [선택된 서브그래프] ──→ aggregate ──→ render ──→ deliver
                      │                                                        ↑
                      └─ 질의 없음 → 전체 선택                        ReferencePort
```

`analyze_query`는 **항상 실행되는 단일 노드**다. 질의가 없으면 LLM을 호출하지 않고 활성 서브그래프 전체를 선택해 반환한다. 노드를 조건부로 만들지 않는 이유는 그래프 형태를 실행마다 바꾸지 않기 위해서다 — 형태가 고정되어야 체크포인트 구조가 안정적이다.

---

## 4. 도메인 모델

### 4-1. BaseContext에 질의 추가

```python
class BaseContext(BaseModel):
    as_of: datetime
    gbm: str
    factory: str
    #: 사람이 준 질의. 스케줄러 실행에서는 None이다.
    query: str | None = None
```

`as_of`와 같은 성격의 **밖에서 주입되는 실행 입력**이므로 여기에 둔다. `_make_subgraph_node`가 `ctx`를 그대로 서브그래프에 넘기므로 추가 배선 없이 모든 서브그래프가 질의를 볼 수 있다.

부수 효과가 하나 있다. `with_cache`의 키가 `f"{label}:{getattr(state, 'ctx', None)}"`이므로 (decorators.py:59) **질의가 다르면 캐시 키도 자동으로 달라진다.** 별도 처리가 필요 없다.

### 4-2. Requirement

```python
class Requirement(BaseModel):
    """질의를 구조화한 결과. 무엇을 볼지와 어디에 초점을 둘지를 담는다."""

    query: str
    #: 서술 초점에 쓰는 관심 키워드
    focus: list[str] = Field(default_factory=list)
    #: 실행할 서브그래프 등록명. 가드레일을 통과한 것만 남는다.
    selected: list[str] = Field(default_factory=list)
    #: 왜 이렇게 골랐는지. 리포트 머리말과 디버깅에 쓴다.
    rationale: str = ""
    #: 가드레일이 버린 이름. 비어 있지 않으면 LLM이 없는 분석을 지목한 것이다.
    dropped: list[str] = Field(default_factory=list)
    #: 질의가 없어 전체를 선택한 경우. 리포트 표기를 달리한다.
    is_full_scope: bool = False
```

### 4-3. ReferenceDoc

```python
class ReferenceDoc(BaseModel):
    """취합 단계가 참고하는 배경 문서.

    id를 두는 이유는 Record와 같다 — 서술이 이 문서를 인용하면
    그 인용을 코드로 대조할 수 있다.
    """

    id: str          # "sop-01", "attach-01"
    source: str      # 파일 경로 또는 "config"
    title: str
    content: str
```

`Record`와 별도 타입으로 두는 이유는 성격이 다르기 때문이다. `Record`는 `as_of` 시점의 관측값이고 `ReferenceDoc`은 시점과 무관한 배경 지식이다. 같은 타입으로 묶으면 `DataRouter`의 kind 라우팅에 얹어야 하는데, 파일 읽기를 저장소 어댑터로 위장시키는 셈이 된다.

---

## 5. 실행 식별자

질의가 실행 범위를 바꾸므로 `as_of`와 동급의 식별자가 되어야 한다. 그렇지 않으면 같은 시각에 질의만 다른 두 실행이 **같은 스레드를 공유**하고, `merge_sections`가 key 기준 병합이라 이전 질의의 섹션이 남는다.

```python
def thread_id_for(gbm: str, factory: str, as_of, query: str | None = None) -> str:
    base = f"{gbm}:{factory}:{as_of:%Y%m%dT%H%M}"
    if not query:
        return base
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]
    return f"{base}:q{digest}"
```

**질의가 없으면 기존 문자열을 그대로 반환한다.** 스케줄러가 쌓아온 체크포인트가 그대로 유효하고, 이 변경 이전·이후 실행이 같은 스레드에서 이어진다.

발송 멱등키도 같은 규칙을 쓴다. 임시 질의 실행이 정규 리포트 파일을 덮어쓰거나 "이미 보냈다"고 오판하는 것을 막는다.

```python
# aggregate.py make_deliver
key = f"{ctx.gbm}_{ctx.factory}_{ctx.as_of:%Y%m%dT%H%M}"
if ctx.query:
    key += f"_q{sha256_8(ctx.query)}"
```

해시 계산은 한 곳에만 둔다. `src/infrastructure/checkpoint.py`에 `query_digest(query) -> str`를 두고 양쪽이 부른다.

---

## 6. analyze_query 노드

`src/application/graph/analyze.py` (신규)

```python
def make_analyze_query(deps: Dependencies, active: list[str], titles: dict[str, str]):
    async def analyze_query(state: ReportState) -> dict:
        query = state.ctx.query
        if not query:
            return {"requirement": Requirement(
                query="", selected=list(active), is_full_scope=True)}
        try:
            req = await deps.llm.plan(ANALYZE_NODE, _prompt(query, active, titles), active)
        except Exception as exc:
            logger.exception("질의 분석 실패 — 전체 분석으로 진행합니다")
            req = Requirement(query=query, selected=list(active), is_full_scope=True)
            drops = [f"{ANALYZE_NODE}: 질의 분석에 실패해 전체 분석으로 진행했습니다 "
                     f"({type(exc).__name__})"]
        else:
            drops = []
            if not req.selected:
                req = req.model_copy(
                    update={"selected": list(active), "is_full_scope": True})
        # 성공·실패 어느 쪽이든 어댑터를 비운다. 실패 직전에 기록된 trace를
        # 두고 가면 다음 노드의 drain에 섞여 엉뚱한 노드 것으로 남는다.
        return {"requirement": req,
                "traces": deps.llm.drain_traces(),
                "guardrail_drops": deps.llm.drain_guardrail_drops() + drops}

    return analyze_query
```

`ANALYZE_NODE = "analyze_query"`는 `aggregate.py`의 `AGGREGATE_NODE`와 같은 방식으로 모듈 상수로 둔다.

**예외 처리를 노드 안에 직접 쓴다.** `with_error_handling`은 로그만 남기고 예외를 다시 던지므로(decorators.py:42) 폴백 용도로 쓸 수 없다.

프롬프트에는 활성 서브그래프의 **등록명과 제목**을 함께 싣는다. `kpi.check`라는 이름만으로는 LLM이 무엇을 하는 분석인지 알 수 없다.

---

## 7. 가드레일

`selected`가 활성 서브그래프 집합의 부분집합인지 **코드가** 검사한다. 위치는 `BaseLLMAdapter`이며, `_enforce_evidence`와 나란히 둔다 — 같은 성격의 검사가 흩어지지 않게 한다.

```python
class BaseLLMAdapter:
    async def plan(self, node: str, prompt: str, allowed: list[str]) -> Requirement:
        """질의 → Requirement. 선택된 이름을 코드로 대조한다."""
        # replay 캐시 확인 → _plan_raw() → _enforce_selection()

    def _enforce_selection(self, node, req, allowed: set[str]) -> Requirement:
        kept = [n for n in req.selected if n in allowed]
        dropped = [n for n in req.selected if n not in allowed]
        if dropped:
            self.guardrail_drops.append(
                f"{node}: 존재하지 않는 분석 {dropped!r}을 선택해 제외했습니다")
        return req.model_copy(update={"selected": kept, "dropped": dropped})
```

**위반한 이름만 버리고 나머지로 진행한다.** `_enforce_evidence`가 개별 판정만 폐기하는 것과 같은 동작이다. 남은 것이 없으면 6절의 노드가 전체 실행으로 되돌린다.

하위 클래스는 `_plan_raw`만 구현한다. `FakeLLMAdapter`는 질의 문자열과 등록명의 토큰이 겹치는 것을 고르고, **일부러 존재하지 않는 이름 하나를 섞는다** — `_judge_raw`가 없는 evidence id를 섞어 가드레일 동작을 보여주는 것과 같은 의도다. `ChatModelAdapter`는 `with_structured_output(Requirement)`를 쓴다.

---

## 8. 조건부 fan-out

```python
graph.add_node("analyze_query", with_timing(make_analyze_query(...), "analyze_query"))
graph.add_edge(START, "analyze_query")
graph.add_conditional_edges("analyze_query", _route, active)   # list[str] 반환 → 병렬
for name in active:
    graph.add_edge(name, "aggregate")


def _route(state: ReportState) -> list[str]:
    req = state.requirement
    return list(req.selected) if req and req.selected else list(active)
```

`_route`는 **State만 보는 순수 함수**다. LLM 호출은 이미 `analyze_query`에서 끝났고, 라우팅 함수는 그 결과를 읽기만 한다. 라우팅 결정이 State에 남아 있으므로 체크포인트로 추적할 수 있다.

> **구현 첫 단계에서 검증할 것** — `aggregate`는 모든 서브그래프에서 정적 엣지를 받는다. 일부만 스케줄될 때 LangGraph가 실행되지 않은 노드를 기다리지 않고 `aggregate`를 실행하는지 확인해야 한다. 표준 동작으로 알고 있으나, 막히면 `aggregate`로 가는 엣지도 조건부로 바꾼다. 이것이 이 스펙의 유일한 미검증 가정이다.

---

## 9. ReferencePort

```python
@runtime_checkable
class ReferencePort(Protocol):
    """취합 단계에 배경 문서를 공급한다.

    지금은 파일을 읽는 정적 어댑터뿐이지만, 질의에 맞춰 검색하는
    어댑터로 교체해도 취합 노드는 바뀌지 않는다.
    """

    async def load(
        self, ctx: BaseContext, requirement: Requirement | None
    ) -> list[ReferenceDoc]: ...
```

`requirement`를 인자로 받는 이유는 정적 어댑터에는 필요 없지만 **검색 어댑터에는 필수**이기 때문이다. 나중에 시그니처를 바꾸면 호출부도 함께 바뀌므로 지금 넣어둔다.

구현은 `src/infrastructure/references.py`에 `StaticReferenceAdapter`로 둔다. config 고정 문서와 CLI 첨부 문서를 함께 받아 `ReferenceDoc` 목록으로 만든다.

**파일 읽기는 조립 시점에 한다.** 파일이 없으면 부팅에서 멈춘다 — `MarkdownRenderer`가 템플릿에 대해 하는 것과 같고, 새벽 실행 중에 발견되는 것보다 낫다.

```json
"report": {
  "template": "report.md",
  "references": ["docs/sop-mx.md"]
}
```

경로는 `PROJECT_ROOT` 기준 상대 경로다. ID는 config 문서가 `sop-01`, `sop-02`…, CLI 첨부가 `attach-01`… 순으로 부여된다.

---

## 10. 인용 사후 검사

`aggregate`는 `judge()`가 아니라 `narrate()`를 쓴다. 자유 서술이라 `_enforce_evidence` 같은 구조화된 검증이 걸리지 않는다.

그래서 **서술에서 대괄호 ID를 뽑아 허용 집합과 대조**한다.

```python
_CITE = re.compile(r"\[([a-z0-9][\w.\-]*)\]")

def _unknown_citations(text: str, allowed: set[str]) -> list[str]:
    return sorted({m for m in _CITE.findall(text) if m not in allowed})
```

허용 집합은 **섹션 key + 참고 문서 ID**다. 없는 ID는 `guardrail_drops`에 기록한다. 서술 자체는 지우지 않는다 — 문장 중간을 잘라내면 읽을 수 없는 글이 되고, 취합 서술은 판정이 아니라 요약이라 폐기 대상이 아니다.

LLM 없이 도는 결정론적 검사이므로 항상 켠다.

---

## 11. Requirement와 참고 문서 전달

`ReportState`에 두 필드를 추가한다.

```python
class ReportState(BaseModel):
    ...
    requirement: Requirement | None = None
    references: list[ReferenceDoc] = Field(default_factory=list)
```

리듀서가 필요 없다. `requirement`는 `analyze_query`가, `references`는 `aggregate`가 각각 한 번만 쓴다.

`_make_subgraph_node`가 `requirement`를 `SubgraphState`로 넘기고, `BaseSubgraph._narrate`가 `focus`가 있을 때만 프롬프트에 한 줄을 덧붙인다.

```python
if state.requirement and state.requirement.focus:
    prompt += f"\n특히 다음에 주목해 서술하세요: {', '.join(focus)}"
```

**`Metric` 값과 임계치 판정은 건드리지 않는다.** 질의가 바꾸는 것은 서술의 초점뿐이다.

`aggregate`는 판정 요약에 참고 문서와 질의 맥락을 더해 프롬프트를 만들고, 참고 문서는 `[sop-01] 제목: 내용` 형태로 싣는다.

---

## 12. 발송 규칙

질의 실행 결과가 정규 수신자 전원에게 나가면 안 된다. 채널 이름을 노드가 알게 하는 대신 **채널에 속성을 둔다.**

```python
class FileDelivery:
    channel = "file"
    broadcast = False

class MailDelivery:
    channel = "mail"
    broadcast = True
```

`DeliveryPort` 프로토콜에 `broadcast: bool`을 추가하고, `deliver` 노드가 `ctx.query`가 있으면 `broadcast` 채널을 건너뛴다. 나중에 Slack 채널이 붙어도 같은 규칙이 자동 적용되고, 노드는 특정 채널 이름을 모른다.

건너뛴 사실은 `ReportRun`에서 확인할 수 있도록 로그를 남긴다.

---

## 13. Config와 CLI

```
run --gbm MX --factory gumi --as-of ... [--query "재고 상황만 알려줘"] [--context PATH ...]
```

`--query`는 선택이다. 주지 않으면 현재와 동일하게 동작한다. `--context`는 반복 가능하다.

`scheduler` 서브커맨드에는 `--query`를 추가하지 않는다. 무인 실행은 항상 전체 범위다.

`run_report()`에 `query: str | None = None`, `context_paths: list[Path] | None = None` 인자를 추가한다. CLI와 스케줄러가 같은 함수를 부른다는 성질은 유지된다.

---

## 14. 변경 파일 목록

| 파일 | 변경 |
|---|---|
| `src/domain/models.py` | `BaseContext.query`, `Requirement`, `ReferenceDoc` 추가 |
| `src/domain/ports.py` | `ReferencePort` 추가, `LLMPort.plan()`, `DeliveryPort.broadcast` |
| `src/infrastructure/llm.py` | `plan()`, `_plan_raw()`, `_enforce_selection()` |
| `src/infrastructure/references.py` | **신규** — `StaticReferenceAdapter` |
| `src/infrastructure/checkpoint.py` | `thread_id_for(query=...)`, `query_digest()` |
| `src/infrastructure/delivery.py` | `broadcast` 속성 |
| `src/application/graph/analyze.py` | **신규** — `make_analyze_query` |
| `src/application/graph/state.py` | `ReportState.requirement`·`references`, `Dependencies.references` |
| `src/application/graph/builder.py` | `analyze_query` 노드, 조건부 fan-out, 참고 어댑터 조립, `requirement` 전달 |
| `src/application/graph/aggregate.py` | 참고 문서·질의 맥락 반영, 인용 검사, 멱등키, 브로드캐스트 제외 |
| `src/application/subgraphs/base.py` | `SubgraphState.requirement`, `_narrate` 초점 반영 |
| `src/application/usecase.py` | `query`·`context_paths` 인자 |
| `src/presentation/cli.py` | `--query`, `--context` |
| `src/presentation/templates/report.md` | 질의 범위 표기 (선택 실행일 때) |
| `config/gbm/mx.json` | `report.references` 예시 |

---

## 15. 테스트 전략

기존 방침(어댑터는 테스트하지 않고 그래프 규약과 순수 로직을 테스트)을 따른다.

**회귀 — 질의 없는 실행이 변하지 않았는가**

- `thread_id_for("MX", "gumi", dt)`가 변경 전과 **문자열이 같다**
- 질의 없이 실행하면 활성 서브그래프가 전부 실행된다
- 질의 없이 실행하면 메일 채널이 건너뛰어지지 **않는다**

**가드레일**

- `selected`에 없는 이름이 섞이면 그것만 `dropped`로 가고 나머지는 실행된다
- 모든 이름이 무효면 전체 실행으로 폴백하고 `is_full_scope`가 참이다
- LLM이 예외를 던지면 전체 실행으로 폴백하고 리포트가 생성된다
- 서술에 없는 인용 ID가 있으면 `guardrail_drops`에 기록된다

**질의 실행**

- 질의가 다르면 `thread_id`가 다르다
- 질의 실행은 파일만 저장하고 메일은 건너뛴다
- 같은 `(as_of, 질의)` 재실행 시 중복 발송이 없다
- 선택된 서브그래프만 `sections`에 나타난다

**참고 문서**

- config 문서와 CLI 첨부가 모두 `references`에 실린다
- 존재하지 않는 경로를 config에 적으면 부팅에서 실패한다
- 참고 문서 ID를 인용한 서술은 `guardrail_drops`에 남지 않는다

---

## 16. 리스크와 미결 사항

**조건부 fan-out과 취합 barrier** (8절) — 유일한 미검증 가정이다. 구현 첫 단계에서 최소 그래프로 확인한다.

**서술 초점의 실효성** — `focus` 키워드를 프롬프트에 넣는 것만으로 서술이 실제로 달라지는지는 `FakeLLMAdapter`로 검증할 수 없다. 실제 모델로 확인이 필요하며, 효과가 미미하면 프롬프트 구조를 조정한다.

**참고 문서의 길이** — 문서가 길면 취합 프롬프트가 비대해진다. 지금은 상한을 두지 않되, 실사용에서 문제가 되면 문서당 길이 제한을 config에 추가한다. 선행 문서 5-2의 "도구 호출이 늘수록 근거 정확도 하락"과는 다른 축의 문제이지만, 컨텍스트가 커질수록 인용 정확도가 떨어질 가능성은 함께 관찰한다.

**`is_full_scope` 표기** — 선택 실행과 전체 실행을 리포트에서 구분할지, 구분한다면 어느 수준으로 표기할지는 템플릿 작업 시 결정한다.

---

## 17. 이 설계가 지키는 것

- 질의가 없으면 **현재 동작과 문자열 단위로 동일**하다
- 서브그래프 단위 실패 격리가 유지된다 — `analyze_query` 실패도 리포트를 죽이지 않는다
- 근거 대조가 유지되고, 인용 검사가 하나 늘어난다
- 부팅 시점 config 검증이 계속 작동한다 — 선택 범위가 `enabled` 집합을 벗어나지 못하므로 `required_kinds` 검증이 그대로 유효하다
- LLM 호출은 여전히 어댑터 하나를 지나며 `LLMTrace`에 기록되고 replay된다
- config를 보는 곳은 여전히 Composition Root뿐이다
