# 질의 분석과 참고 문서 반영 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자의 자연어 질의를 받아 실행할 분석의 범위와 서술 초점을 조정하고, 운영 기준 문서를 취합 단계에 반영한다.

**Architecture:** `START`와 서브그래프 fan-out 사이에 `analyze_query` 노드를 넣는다. 이 노드는 LLM을 **1회** 호출해 질의를 `Requirement`(초점·대상 분석·사유)로 바꾸고, 코드가 그 선택을 활성 서브그래프 집합과 대조한다. 조건부 엣지가 선택된 분석만 병렬 실행하고, 취합 노드는 `ReferencePort`에서 받은 배경 문서를 함께 본다. 질의가 없으면 LLM을 부르지 않고 전체를 선택하므로 **스케줄러 동작이 문자열 단위로 그대로 유지된다.**

**Tech Stack:** Python 3, LangGraph 1.2.11, LangChain Core 1.5.4, Pydantic 2.13.4, pytest 9.1.1

## Global Constraints

- **질의가 없으면 현재 동작과 완전히 동일해야 한다.** `thread_id` 문자열, 실행되는 서브그래프, 발송 채널이 모두 그대로.
- **숫자는 LLM이 다시 쓰지 않는다.** 질의가 바꾸는 것은 서술의 초점뿐이며 `Metric` 값과 임계치 판정은 건드리지 않는다.
- **가드레일은 코드가, 전수로 수행한다.** LLM 판단으로 검사 여부를 결정하지 않는다.
- **config를 보는 곳은 Composition Root(`builder.py`)뿐이다.** 노드는 config 객체를 들고 다니지 않는다.
- **`as_of`는 밖에서 주입된다.** 노드 안에서 `datetime.now()`를 부르지 않는다.
- 주석과 사용자 노출 문자열은 한국어. 기존 코드의 어조(설명은 "왜"를 적고, 자명한 "무엇"은 적지 않는다)를 따른다.
- **테스트는 `unittest.TestCase`로 쓴다.** 기존 9개 파일이 전부 그렇고, `tests/README.md`가 "pytest 없이도 돕니다"를 보증한다. 함수형 pytest 테스트나 `import pytest`를 쓰면 `python -m unittest discover`가 신규 테스트를 수집하지 못하고 pytest가 필수 의존이 된다. 예외 검사는 `pytest.raises`가 아니라 `with self.assertRaises(...)`를 쓴다.
- 테스트는 어댑터가 아니라 **그래프 규약과 순수 로직**을 대상으로 한다 (`tests/README.md`). **예외:** `StaticReferenceAdapter`는 외부 의존이 없는 파일 읽기이고 "경로가 틀리면 부팅에서 멈춘다"가 핵심 동작이라 직접 테스트한다 (Task 6에서 README에 사유를 남긴다).
- 실행 명령: 테스트는 `.venv/bin/python -m pytest` 또는 `.venv/bin/python -m unittest discover -s tests -t .` (**둘 다 통과해야 한다**), 앱은 `.venv/bin/python -m src`.
- `FakeLLMAdapter`는 가드레일이 실제로 도는지 LLM 없이 보이려고 **의도적으로 잘못된 값을 섞는** 기존 규약이 있다 (`_judge_raw`가 `f"{ev}::hallucinated"`를 넣는다). 신규 `_plan_raw`도 같은 방식을 따른다.

**검증 완료된 전제:** LangGraph 1.2.11에서 조건부 fan-out으로 일부 노드만 스케줄해도, 모든 서브그래프에서 정적 엣지를 받는 `aggregate`가 정상 실행된다. 실행 안 된 노드는 barrier를 막지 않는다. (설계 문서 8절의 미검증 항목이었으며 최소 그래프로 확인함.)

---

## File Structure

| 파일 | 책임 |
|---|---|
| `src/domain/models.py` | `BaseContext.query`, `Requirement`, `ReferenceDoc` — 기술을 모르는 순수 모델 |
| `src/domain/ports.py` | `ReferencePort` 계약, `LLMPort.plan`, `DeliveryPort.broadcast` |
| `src/infrastructure/llm.py` | `plan()` 단일 경로 + `_enforce_selection` 가드레일 |
| `src/infrastructure/references.py` | **신규** — 파일에서 배경 문서를 읽는 정적 어댑터 |
| `src/infrastructure/checkpoint.py` | `query_digest()` — 질의 해시를 만드는 **유일한** 곳 |
| `src/infrastructure/delivery.py` | 채널의 `broadcast` 표시 |
| `src/application/graph/analyze.py` | **신규** — `analyze_query` 노드와 프롬프트 |
| `src/application/graph/state.py` | `ReportState.requirement`·`references`, `Dependencies.references` |
| `src/application/graph/builder.py` | 노드 조립, 조건부 fan-out, 참고 어댑터 주입 |
| `src/application/graph/aggregate.py` | 참고 문서 반영, 인용 검사, 멱등키, 브로드캐스트 제외 |
| `src/application/subgraphs/base.py` | `SubgraphState.requirement`, 서술 초점 |
| `src/presentation/renderers.py` | 리포트 머리말의 질의 범위 표기 |
| `src/presentation/cli.py` | `--query`, `--context` |
| `src/application/usecase.py` | 두 인자를 그래프까지 배선 |

---

### Task 1: 질의를 실행 식별자에 넣기

`BaseContext`에 질의를 싣고, 질의 해시를 만드는 함수를 한 곳에 둔다. 이후 모든 태스크가 이것을 쓴다.

**Files:**
- Modify: `src/domain/models.py:15-24` (`BaseContext`)
- Modify: `src/infrastructure/checkpoint.py:131-138` (`thread_id_for`)
- Test: `tests/test_checkpoint.py`

**Interfaces:**
- Produces: `BaseContext.query: str | None`, `query_digest(query: str | None) -> str` (질의 없으면 `""`), `thread_id_for(gbm, factory, as_of, query=None) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_checkpoint.py` 끝에 추가:

```python
class QueryThreadIdTest(unittest.TestCase):
    """질의가 실행 범위를 바꾸므로 as_of와 동급의 식별자여야 한다."""

    def test_unchanged_without_query(self):
        """질의가 없으면 기존 문자열 그대로. 스케줄러 체크포인트가 살아있어야 한다."""
        self.assertEqual(thread_id_for("mx", "gumi", AS_OF), "mx:gumi:20260813T0800")

    def test_differs_by_query(self):
        a = thread_id_for("mx", "gumi", AS_OF, "재고만")
        b = thread_id_for("mx", "gumi", AS_OF, "설비만")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("mx:gumi:20260813T0800:q"))

    def test_stable_for_same_query(self):
        self.assertEqual(
            thread_id_for("mx", "gumi", AS_OF, "재고만"),
            thread_id_for("mx", "gumi", AS_OF, "재고만"),
        )

    def test_digest_empty_without_query(self):
        self.assertEqual(query_digest(None), "")
        self.assertEqual(query_digest(""), "")
        self.assertEqual(len(query_digest("재고만")), 8)
```

파일 상단 `from src.infrastructure.checkpoint import (...)` 목록에 `query_digest`를 추가한다. `unittest`와 `AS_OF`는 이미 import돼 있다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_checkpoint.py -v`
Expected: FAIL — `ImportError: cannot import name 'query_digest'`

- [ ] **Step 3: 최소 구현**

`src/domain/models.py`의 `BaseContext`에 필드 추가:

```python
class BaseContext(BaseModel):
    """모든 실행이 들고 다니는 기준. as_of는 반드시 밖에서 주입된다.

    노드 안에서 datetime.now()를 부르면 재개(Durable Execution)와
    Time Travel, 멱등성이 모두 깨진다.
    """

    as_of: datetime
    gbm: str
    factory: str
    #: 사람이 준 질의. 스케줄러 실행에서는 None이다.
    query: str | None = None
```

`src/infrastructure/checkpoint.py` 상단에 `import hashlib`을 추가하고 `thread_id_for`를 교체:

```python
def query_digest(query: str | None) -> str:
    """질의를 식별자에 넣기 위한 짧은 해시. 질의가 없으면 빈 문자열.

    thread_id와 발송 멱등키가 같은 값을 써야 하므로 계산은 여기 한 곳에만 둔다.
    """
    if not query:
        return ""
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]


def thread_id_for(gbm: str, factory: str, as_of, query: str | None = None) -> str:
    """실행 하나를 식별하는 키.

    같은 (gbm, factory, as_of, query)는 같은 스레드다. 질의가 실행 범위를
    바꾸므로 as_of와 동급의 식별자여야 한다 — 빠뜨리면 같은 시각에 질의만
    다른 두 실행이 한 체크포인트를 공유해 이전 질의의 섹션이 남는다.

    질의가 없으면 기존 문자열을 그대로 돌려준다. 이 변경 이전에 쌓인
    체크포인트가 계속 유효해야 하기 때문이다.
    """
    base = f"{gbm}:{factory}:{as_of:%Y%m%dT%H%M}"
    digest = query_digest(query)
    return f"{base}:q{digest}" if digest else base
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_checkpoint.py -v`
Expected: PASS (신규 4건 포함 전부)

- [ ] **Step 5: 전체 회귀를 확인한다**

Run: `.venv/bin/python -m pytest`
Expected: PASS — `BaseContext`에 기본값 필드를 더한 것이라 기존 호출부가 깨지지 않는다.

- [ ] **Step 6: 커밋**

```bash
git add src/domain/models.py src/infrastructure/checkpoint.py tests/test_checkpoint.py
git commit -m "Carry the user query on BaseContext and in the thread id

thread_id_for keeps returning the current string when no query is given,
so checkpoints written before this change stay valid and the scheduler
resumes exactly as it did."
```

---

### Task 2: Requirement 모델과 plan() 가드레일

질의를 구조화하는 LLM 경로를 만든다. 선택된 이름을 코드가 대조하는 가드레일이 핵심이며, `_enforce_evidence` 바로 옆에 둔다.

**Files:**
- Modify: `src/domain/models.py` (`Requirement` 추가)
- Modify: `src/domain/ports.py:65-76` (`LLMPort`)
- Modify: `src/infrastructure/llm.py` (`plan`, `_plan_raw`, `_enforce_selection`)
- Test: `tests/test_query_planning.py` (신규)

**Interfaces:**
- Consumes: 없음 (Task 1과 독립)
- Produces: `Requirement(query, focus, selected, rationale, dropped, is_full_scope)`, `BaseLLMAdapter.plan(node: str, prompt: str, allowed: list[str]) -> Requirement`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_query_planning.py` 신규:

```python
"""질의 분석의 구조화 출력과 가드레일.

LLM이 없는 분석 이름을 지목해도 리포트가 정상 생성되어야 한다. 이 검사는
LLM 없이 도는 결정론적 대조라 항상 켜 둔다.
"""

from __future__ import annotations

import asyncio
import unittest

from src.domain.models import Requirement
from src.infrastructure.llm import FakeLLMAdapter

ALLOWED = ["material.stock", "kpi.check", "line.equipment"]


def plan(prompt: str, allowed: list[str] | None = None):
    llm = FakeLLMAdapter(model="fake-local", seed="test")
    # `allowed or ALLOWED`로 쓰면 빈 목록이 falsy라 기본값으로 새어, 정작
    # "허용된 분석이 없을 때"를 검증하는 테스트가 그 경로를 타지 못한다.
    req = asyncio.run(
        llm.plan("analyze_query", prompt, ALLOWED if allowed is None else allowed)
    )
    return req, llm


class SelectionGuardrailTest(unittest.TestCase):
    """검사기가 LLM이 아니라 집합 연산이므로 검사 자체가 틀릴 수 없다."""

    def test_keeps_only_registered_names(self):
        req, _ = plan("stock 상황을 알려줘")
        self.assertIn("material.stock", req.selected)
        for name in req.selected:
            self.assertIn(name, ALLOWED)

    def test_records_dropped_names(self):
        req, llm = plan("stock 상황을 알려줘")
        self.assertTrue(req.dropped, "Fake는 가드레일 시연용으로 없는 이름을 하나 섞는다")
        for name in req.dropped:
            self.assertNotIn(name, ALLOWED)
        self.assertTrue(any(req.dropped[0] in d for d in llm.guardrail_drops))

    def test_records_trace(self):
        """프롬프트와 응답이 남아야 replay가 가능하다."""
        _, llm = plan("stock 상황을 알려줘")
        traces = llm.drain_traces()
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].node, "analyze_query")
        self.assertIn("stock", traces[0].prompt)

    def test_full_scope_when_nothing_allowed(self):
        req, _ = plan("아무거나", allowed=[])
        self.assertEqual(req.selected, [])
        self.assertTrue(req.is_full_scope)

    def test_requirement_defaults_are_empty(self):
        req = Requirement()
        self.assertEqual(req.query, "")
        self.assertEqual(req.selected, [])
        self.assertFalse(req.is_full_scope)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_query_planning.py -v`
Expected: FAIL — `ImportError: cannot import name 'Requirement'`

- [ ] **Step 3: 모델을 추가한다**

`src/domain/models.py`의 `Judgement` 위, "판정" 구분선 앞에 넣는다:

```python
# --------------------------------------------------------------------------
# 질의
# --------------------------------------------------------------------------
class Requirement(BaseModel):
    """질의를 구조화한 결과. 무엇을 볼지와 어디에 초점을 둘지를 담는다.

    selected는 가드레일을 통과한 이름만 남는다. 원본에 없는 이름이 있었다면
    dropped에 남으므로, 리포트를 보고 LLM이 무엇을 지어냈는지 역추적할 수 있다.
    """

    query: str = ""
    #: 서술 초점에 쓰는 관심 키워드
    focus: list[str] = Field(default_factory=list)
    #: 실행할 서브그래프 등록명
    selected: list[str] = Field(default_factory=list)
    rationale: str = ""
    #: 가드레일이 버린 이름
    dropped: list[str] = Field(default_factory=list)
    #: 질의가 없거나 분석에 실패해 전체를 선택한 경우
    is_full_scope: bool = False
```

- [ ] **Step 4: 어댑터에 plan 경로를 만든다**

`src/infrastructure/llm.py` 상단 import에 `Requirement`를 추가한다.

`BaseLLMAdapter`의 `_judge_raw` 아래에 추상 메서드를 더한다:

```python
    async def _plan_raw(self, prompt: str, allowed: list[str]) -> Requirement:
        """프롬프트 → 구조화된 실행 계획. 검증 전 raw."""
        raise NotImplementedError
```

`judge()` 아래, `_enforce_evidence` 위에 넣는다:

```python
    async def plan(self, node: str, prompt: str, allowed: list[str]) -> Requirement:
        """질의를 실행 계획으로 바꾸고 선택된 이름을 코드로 대조한다.

        judge()가 evidence를 대조하는 것과 같은 구조다. 검사기가 LLM이 아니라
        집합 연산이므로 검사 자체가 틀릴 수 없다.
        """
        if not allowed:
            return Requirement(is_full_scope=True)

        key = self._replay_key(node, prompt)
        if key in self._replay:
            payload = self._replay[key]
            raw = Requirement(**json.loads(payload))
            self._record(node, prompt, payload, replayed=True)
        else:
            raw = await self._plan_raw(prompt, allowed)
            payload = json.dumps(raw.model_dump(mode="json"), ensure_ascii=False)
            self._record(node, prompt, payload)

        return self._enforce_selection(node, raw, set(allowed))

    def _enforce_selection(
        self, node: str, req: Requirement, allowed: set[str]
    ) -> Requirement:
        kept = [n for n in req.selected if n in allowed]
        dropped = [n for n in req.selected if n not in allowed]
        if dropped:
            self.guardrail_drops.append(
                f"{node}: 등록되지 않은 분석 {dropped!r}을 선택해 제외했습니다"
            )
        return req.model_copy(update={"selected": kept, "dropped": dropped})
```

`FakeLLMAdapter`에 추가한다:

```python
    async def _plan_raw(self, prompt: str, allowed: list[str]) -> Requirement:
        """프롬프트에 이름이 언급된 분석을 고른다.

        _judge_raw와 마찬가지로 **일부러 없는 이름을 하나 섞는다.** 가드레일이
        실제로 동작하는지 LLM 없이 확인할 수 있어야 하기 때문이다.
        """
        hits = [n for n in allowed if n in prompt or n.split(".")[-1] in prompt]
        picked = hits or allowed[:1]
        return Requirement(
            focus=[n.split(".")[-1] for n in picked],
            selected=[*picked, "nonexistent.analysis"],
            rationale="질의에 언급된 분석을 선택했습니다.",
        )
```

`ChatModelAdapter`에 추가한다:

```python
    async def _plan_raw(self, prompt: str, allowed: list[str]) -> Requirement:
        """★ 실제 호출 지점 (실행 계획).

        도구 호출도 루프도 없는 단발 호출이다. 판단에 필요한 재료가 프롬프트에
        모두 들어 있으므로 반복할 이유가 없다.
        """
        listing = "\n".join(f"  {n}" for n in allowed)
        structured = self._ensure_client().with_structured_output(Requirement)
        return await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "당신은 제조 운영 리포트의 범위를 정합니다. selected에는 "
                        "아래 목록에 있는 이름만 넣으세요. 목록에 없는 이름을 "
                        "만들어내면 그 선택은 폐기됩니다.\n" + listing
                    ),
                },
                {"role": "user", "content": prompt},
            ]
        )
```

`src/domain/ports.py`의 `LLMPort`에 계약을 더한다 (import에 `Requirement` 추가):

```python
    async def plan(
        self, node: str, prompt: str, allowed: list[str]
    ) -> Requirement: ...
```

- [ ] **Step 5: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_query_planning.py -v`
Expected: PASS (5건)

- [ ] **Step 6: 커밋**

```bash
git add src/domain/models.py src/domain/ports.py src/infrastructure/llm.py tests/test_query_planning.py
git commit -m "Turn a query into a Requirement through one guarded LLM call

The selection guardrail drops only the names that are not registered,
mirroring how _enforce_evidence drops individual judgements, and the
fake adapter deliberately emits one bogus name so the check is
exercised without a real model."
```

---

### Task 3: analyze_query 노드

질의가 없으면 LLM을 부르지 않고, 실패하면 전체 실행으로 되돌린다.

**Files:**
- Create: `src/application/graph/analyze.py`
- Modify: `src/application/graph/state.py` (`ReportState.requirement`)
- Test: `tests/test_query_planning.py` (이어서 추가)

**Interfaces:**
- Consumes: `Requirement`, `BaseLLMAdapter.plan` (Task 2), `BaseContext.query` (Task 1)
- Produces: `ANALYZE_NODE: str = "analyze_query"`, `make_analyze_query(deps, active: list[str], titles: dict[str, str]) -> node`, `ReportState.requirement: Requirement | None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_query_planning.py`에 추가. 상단 import에 다음을 보강한다:

```python
from src.application.graph.analyze import make_analyze_query
from src.application.graph.state import Dependencies, ReportState
from src.domain.models import BaseContext
from tests.helpers import AS_OF
```

본문에 추가:

```python
TITLES = {"material.stock": "자재 소진 예상", "kpi.check": "KPI 점검",
          "line.equipment": "라인별 장비 상태"}


def run_node(query, llm=None):
    deps = Dependencies(llm=llm or FakeLLMAdapter(model="fake-local", seed="test"))
    node = make_analyze_query(deps, ALLOWED, TITLES)
    ctx = BaseContext(as_of=AS_OF, gbm="mx", factory="gumi", query=query)
    return asyncio.run(node(ReportState(ctx=ctx)))


class AnalyzeQueryNodeTest(unittest.TestCase):
    def test_no_query_selects_everything_without_calling_llm(self):
        """스케줄러 경로. LLM을 부르지 않아야 비용도 지연도 늘지 않는다."""
        out = run_node(None)
        req = out["requirement"]
        self.assertEqual(req.selected, ALLOWED)
        self.assertTrue(req.is_full_scope)
        self.assertFalse(out.get("traces"))

    def test_query_narrows_selection(self):
        out = run_node("stock 상황을 알려줘")
        req = out["requirement"]
        self.assertEqual(req.selected, ["material.stock"])
        self.assertEqual(req.query, "stock 상황을 알려줘")
        self.assertFalse(req.is_full_scope)

    def test_prompt_carries_titles(self):
        """등록명만으로는 LLM이 무슨 분석인지 알 수 없다."""
        out = run_node("stock 상황을 알려줘")
        self.assertTrue(out["traces"], "질의가 있으면 trace가 남아야 한다")
        self.assertIn("자재 소진 예상", out["traces"][0].prompt)

    def test_llm_failure_falls_back_to_full_scope(self):
        class Boom(FakeLLMAdapter):
            async def _plan_raw(self, prompt, allowed):
                raise RuntimeError("모델 응답 없음")

        out = run_node("stock 상황", llm=Boom(model="fake-local"))
        req = out["requirement"]
        self.assertEqual(req.selected, ALLOWED)
        self.assertTrue(req.is_full_scope)
        self.assertTrue(any("RuntimeError" in d for d in out["guardrail_drops"]))

    def test_empty_selection_falls_back_to_full_scope(self):
        class Empty(FakeLLMAdapter):
            async def _plan_raw(self, prompt, allowed):
                return Requirement(selected=["nonexistent.only"])

        out = run_node("무관한 질의", llm=Empty(model="fake-local"))
        self.assertEqual(out["requirement"].selected, ALLOWED)
        self.assertTrue(out["requirement"].is_full_scope)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_query_planning.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.application.graph.analyze'`

- [ ] **Step 3: State에 필드를 더한다**

`src/application/graph/state.py`의 `ReportState`에 추가 (import에 `Requirement` 보강):

```python
    #: 질의 분석 결과. analyze_query가 한 번만 쓰므로 리듀서가 없다.
    requirement: Requirement | None = None
```

- [ ] **Step 4: 노드를 만든다**

`src/application/graph/analyze.py` 신규:

```python
"""질의 분석 노드 — START와 서브그래프 fan-out 사이.

LLM을 **1회** 부른다. 도구 호출도 루프도 없다. 판단에 필요한 재료(질의,
활성 분석 목록)가 프롬프트에 모두 들어 있고 답도 한 번에 나오기 때문이다.

질의가 없으면 LLM을 아예 부르지 않고 전체를 선택한다. 무인 스케줄러가
이 경로로 도므로 비용도 지연도 늘지 않는다.
"""

from __future__ import annotations

import logging

from src.application.graph.state import Dependencies, ReportState
from src.domain.models import Requirement

logger = logging.getLogger(__name__)

ANALYZE_NODE = "analyze_query"


def _prompt(query: str, active: list[str], titles: dict[str, str]) -> str:
    """등록명과 제목을 함께 싣는다. 'kpi.check'만으로는 무슨 분석인지 모른다."""
    listing = "\n".join(f"- {name}: {titles.get(name, name)}" for name in active)
    return (
        "아래는 이 리포트에서 수행할 수 있는 분석 목록입니다.\n"
        f"{listing}\n\n"
        "사용자 질의에 답하는 데 필요한 분석만 selected에 고르고, 서술에서 "
        "강조할 키워드를 focus에 넣으세요. 목록에 없는 이름은 쓰지 마세요.\n\n"
        f"질의: {query}"
    )


def make_analyze_query(deps: Dependencies, active: list[str], titles: dict[str, str]):
    async def analyze_query(state: ReportState) -> dict:
        query = state.ctx.query
        if not query:
            return {"requirement": Requirement(selected=list(active), is_full_scope=True)}

        try:
            req = await deps.llm.plan(ANALYZE_NODE, _prompt(query, active, titles), active)
        except Exception as exc:  # noqa: BLE001 - 여기서 막지 않으면 리포트가 통째로 죽는다
            logger.exception("질의 분석 실패 — 전체 분석으로 진행합니다")
            req = Requirement(query=query, selected=list(active), is_full_scope=True)
            drops = [
                f"{ANALYZE_NODE}: 질의 분석에 실패해 전체 분석으로 진행했습니다 "
                f"({type(exc).__name__})"
            ]
        else:
            drops = []
            req = req.model_copy(update={"query": query})
            if not req.selected:
                # 가드레일이 전부 걷어냈다. 빈 리포트보다 전체 리포트가 낫다.
                req = req.model_copy(
                    update={"selected": list(active), "is_full_scope": True}
                )

        # 성공·실패 어느 쪽이든 어댑터를 비운다. 실패 직전에 기록된 trace를 두고
        # 가면 다음 노드의 drain에 섞여 엉뚱한 노드 것으로 남는다.
        return {
            "requirement": req,
            "traces": deps.llm.drain_traces(),
            "guardrail_drops": deps.llm.drain_guardrail_drops() + drops,
        }

    return analyze_query
```

> `with_error_handling`으로 감싸지 않는다. 그 데코레이터는 로그만 남기고 예외를 다시 던지므로(`decorators.py:42`) 폴백에 쓸 수 없다.

- [ ] **Step 5: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_query_planning.py -v`
Expected: PASS (10건)

- [ ] **Step 6: 커밋**

```bash
git add src/application/graph/analyze.py src/application/graph/state.py tests/test_query_planning.py
git commit -m "Add the analyze_query node with a full-scope fallback

No query means no LLM call at all, so the scheduler path costs exactly
what it costs today. A failed or fully-rejected plan falls back to
running everything, because an empty report is worse than a broad one."
```

---

### Task 4: 조건부 fan-out 배선

`START → analyze_query → 선택된 분석`으로 그래프를 바꾼다. 취합으로 가는 정적 엣지는 그대로 둔다 — 실행되지 않은 노드는 barrier를 막지 않는다(전제 검증 완료).

**Files:**
- Modify: `src/application/graph/builder.py:220-258` (`build_graph`)
- Test: `tests/test_query_routing.py` (신규)

**Interfaces:**
- Consumes: `ANALYZE_NODE`, `make_analyze_query` (Task 3)
- Produces: 그래프 형태 — `analyze_query` 노드가 항상 실행되고, `state.requirement.selected`에 있는 서브그래프만 실행된다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_query_routing.py` 신규:

```python
"""질의에 따른 라우팅. 그래프 규약을 본다.

질의가 없을 때 지금과 동일하게 도는지가 가장 중요한 회귀 항목이다.
"""

from __future__ import annotations

import unittest

from tests.helpers import run_graph, temp_config, with_subgraph_patch


def _cfg():
    return with_subgraph_patch(lambda c: None)


class QueryRoutingTest(unittest.TestCase):
    def test_no_query_runs_every_enabled_subgraph(self):
        with temp_config(gbm=_cfg()) as cfg:
            state = run_graph(cfg)
        keys = {s.key for s in state["sections"]}
        self.assertIn("material.stock", keys)
        self.assertIn("kpi.check", keys)
        self.assertGreaterEqual(len(keys), 5, f"전체가 돌아야 한다: {keys}")

    def test_no_query_leaves_requirement_full_scope(self):
        with temp_config(gbm=_cfg()) as cfg:
            state = run_graph(cfg)
        self.assertTrue(state["requirement"].is_full_scope)

    def test_query_runs_only_selected_subgraphs(self):
        """Fake 어댑터는 프롬프트에 이름이 있는 분석을 고른다."""
        with temp_config(gbm=_cfg()) as cfg:
            state = run_graph(cfg, query="material.stock 만 보여줘")
        self.assertEqual({s.key for s in state["sections"]}, {"material.stock"})

    def test_aggregate_runs_even_when_some_subgraphs_are_skipped(self):
        """일부만 스케줄돼도 취합 barrier가 막히지 않아야 한다."""
        with temp_config(gbm=_cfg()) as cfg:
            state = run_graph(cfg, query="material.stock 만 보여줘")
        self.assertIsNotNone(state["overall"])
        self.assertTrue(state["rendered"])
```

`tests/helpers.py`의 `run_graph`/`run_graph_with`에 질의를 넘길 수 있어야 한다. 두 함수를 고친다:

```python
def run_graph(cfg: DeployConfig, as_of: datetime = AS_OF, **kwargs) -> dict:
    """그래프를 끝까지 돌리고 최종 State를 돌려준다."""
    return run_graph_with(cfg, as_of=as_of, **kwargs)[0]


def run_graph_with(
    cfg: DeployConfig,
    as_of: datetime = AS_OF,
    replay: dict[str, str] | None = None,
    query: str | None = None,
) -> tuple[dict, Any, dict]:
    """State와 함께 그래프·config도 돌려준다. Time Travel 테스트에 필요하다."""
    env = EnvConfig()
    deps = build_dependencies(cfg, env, replay=replay)
    graph = build_graph(cfg, deps, env, replay=replay)
    ctx = BaseContext(as_of=as_of, gbm=GBM, factory=FACTORY, query=query)
    run_config = {
        "configurable": {"thread_id": thread_id_for(GBM, FACTORY, as_of, query)}
    }
    state = asyncio.run(graph.ainvoke(ReportState(ctx=ctx), config=run_config))
    return state, graph, run_config
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_query_routing.py -v`
Expected: FAIL — `test_no_query_leaves_requirement_full_scope`에서 `KeyError: 'requirement'` (아직 노드가 그래프에 없다)

- [ ] **Step 3: 그래프를 다시 엮는다**

`src/application/graph/builder.py` import에 추가:

```python
from src.application.graph.analyze import ANALYZE_NODE, make_analyze_query
```

`build_graph`의 서브그래프 루프에서 `graph.add_edge(START, name)` 한 줄을 **지운다**. 루프는 이렇게 남는다:

```python
        node = _make_subgraph_node(name, instance.compile())
        node = with_cache(node, name, parsed[name].cache_ttl)
        node = with_timing(node, name)
        graph.add_node(name, node)
        graph.add_edge(name, "aggregate")    # 전부 끝나야 도는 자연스러운 barrier
```

루프 **뒤에**, `aggregate` 노드를 붙이기 전에 넣는다:

```python
    # 질의 분석이 fan-out 앞에 선다. 질의가 없으면 LLM을 부르지 않으므로
    # 스케줄러 경로의 비용과 지연은 그대로다.
    titles = {name: (get_subgraph(name).title or name) for name in active}
    graph.add_node(
        ANALYZE_NODE,
        with_timing(make_analyze_query(deps, active, titles), ANALYZE_NODE),
    )
    graph.add_edge(START, ANALYZE_NODE)
    # 목적지 목록을 반환하면 LangGraph가 그만큼 병렬로 띄운다. 스케줄되지
    # 않은 노드는 aggregate의 barrier를 막지 않는다.
    graph.add_conditional_edges(ANALYZE_NODE, _route_selected(active), active)
```

파일 하단 `_slot_attr` 아래에 라우팅 함수를 둔다:

```python
def _route_selected(active: list[str]):
    """State만 보는 순수 함수. LLM 호출은 analyze_query에서 이미 끝났다.

    라우팅 결정이 State에 남아 있으므로 체크포인트로 추적할 수 있다.
    """

    def route(state: ReportState) -> list[str]:
        req = state.requirement
        return list(req.selected) if req and req.selected else list(active)

    return route
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_query_routing.py -v`
Expected: PASS (4건)

- [ ] **Step 5: 전체 회귀를 확인한다**

Run: `.venv/bin/python -m pytest`
Expected: PASS — 특히 `tests/test_graph_behaviour.py`가 그대로 통과해야 한다. 질의 없는 경로가 바뀌지 않았다는 증거다.

- [ ] **Step 6: 실제 실행으로 눈으로 확인한다**

Run: `.venv/bin/python -m src run --gbm mx --factory gumi --stream --quiet`
Expected: 진행 표시에 `· analyze_query`가 먼저 나오고, 이어서 서브그래프 6개가 전부 나온다.

- [ ] **Step 7: 커밋**

```bash
git add src/application/graph/builder.py tests/test_query_routing.py tests/helpers.py
git commit -m "Route the fan-out through analyze_query

Subgraphs now hang off a conditional edge instead of START. The static
edges into aggregate stay: LangGraph does not wait on nodes it never
scheduled, verified against 1.2.11 with a minimal graph."
```

---

### Task 5: 서술 초점 전달

`Requirement`를 서브그래프까지 내려보내 세부 요약의 초점만 바꾼다. 숫자와 판정은 건드리지 않는다.

**Files:**
- Modify: `src/application/subgraphs/base.py:54-65` (`SubgraphState`), `:170-177` (`_narrate`)
- Modify: `src/application/graph/builder.py:152-172` (`_make_subgraph_node`)
- Test: `tests/test_subgraph_nodes.py` (추가)

**Interfaces:**
- Consumes: `ReportState.requirement` (Task 3)
- Produces: `SubgraphState.requirement: Requirement | None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_subgraph_nodes.py` 끝에 추가:

파일 상단 import에 다음을 보강한다 (`asyncio`, `unittest`, `AS_OF`는 이미 있다):

```python
from src.application.graph.state import Dependencies
from src.application.subgraphs.kpi.check import KpiCheck, KpiCheckConfig
from src.domain.models import Requirement, SnapshotContext
from src.infrastructure.llm import FakeLLMAdapter
```

본문에 추가:

```python
class NarrationFocusTest(unittest.TestCase):
    """질의가 바꾸는 것은 서술의 초점뿐이다. 숫자와 판정은 그대로다."""

    def _narrate_with(self, requirement):
        llm = FakeLLMAdapter(model="fake-local", seed="t")
        sub = KpiCheck(KpiCheckConfig(enabled=True), Dependencies(llm=llm))
        scoped = SnapshotContext(as_of=AS_OF, gbm="mx", factory="gumi")
        state = SubgraphState(
            ctx=scoped,
            scoped=scoped,
            metrics=[Metric(name="수율", value=97.1, unit="%")],
            requirement=requirement,
        )
        asyncio.run(sub.generate_output(state))
        return llm.drain_traces()[0].prompt

    def test_focus_reaches_the_narration_prompt(self):
        prompt = self._narrate_with(Requirement(query="수율", focus=["수율", "불량"]))
        self.assertIn("수율, 불량", prompt)

    def test_metrics_are_unchanged_by_focus(self):
        prompt = self._narrate_with(Requirement(query="수율", focus=["수율"]))
        self.assertIn("수율: 97.1%", prompt)

    def test_no_requirement_leaves_prompt_unchanged(self):
        self.assertNotIn("주목해", self._narrate_with(None))
```

`SubgraphState`와 `Metric`은 이 파일이 이미 import하고 있다. 없으면 함께 보강한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_subgraph_nodes.py -k focus -v`
Expected: FAIL — `ValidationError: Object has no attribute 'requirement'`

- [ ] **Step 3: 구현한다**

`src/application/subgraphs/base.py`의 `SubgraphState`에 필드를 더한다 (import에 `Requirement` 보강):

```python
    #: 질의 분석 결과. 서술의 초점에만 쓰고 숫자와 판정은 건드리지 않는다.
    requirement: Requirement | None = None
```

같은 파일 `_narrate`를 교체한다:

```python
    async def _narrate(self, state: SubgraphState) -> str:
        facts = "\n".join(f"- {m.name}: {m.value}{m.unit} {m.note}".rstrip()
                          for m in state.metrics)
        prompt = (
            f"[{self.title}] 아래 사실을 2~3문장으로 요약하세요. "
            f"숫자를 새로 만들지 말고 아래 값만 인용하세요.\n{facts}"
        )
        # 질의가 바꾸는 것은 서술의 초점뿐이다. 위의 사실 목록은 그대로다.
        if state.requirement and state.requirement.focus:
            prompt += (
                "\n\n특히 다음에 주목해 서술하세요: "
                f"{', '.join(state.requirement.focus)}"
            )
        return await self.deps.llm.narrate(self.registry_name, prompt)
```

`src/application/graph/builder.py`의 `_make_subgraph_node`에서 서브그래프 State를 만들 때 함께 넘긴다:

```python
    async def node(state: ReportState) -> dict:
        result = await compiled.ainvoke(
            SubgraphState(ctx=state.ctx, requirement=state.requirement)
        )
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_subgraph_nodes.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/application/subgraphs/base.py src/application/graph/builder.py tests/test_subgraph_nodes.py
git commit -m "Let the query steer section narration only

The focus keywords are appended to the narration prompt; the metric
list handed to the model is unchanged, so the numbers in the report
still come from State rather than from the model."
```

---

### Task 6: ReferencePort와 정적 어댑터

배경 문서를 공급하는 포트를 만든다. 나중에 검색 어댑터로 갈아끼울 자리다.

**Files:**
- Modify: `src/domain/models.py` (`ReferenceDoc`)
- Modify: `src/domain/ports.py` (`ReferencePort`)
- Create: `src/infrastructure/references.py`
- Modify: `src/application/graph/state.py` (`Dependencies.references`, `ReportState.references`)
- Modify: `src/application/graph/builder.py:142-149` (`build_dependencies` 반환)
- Modify: `tests/README.md` ("아직 없는 것"의 어댑터 항목에 예외 사유)
- Test: `tests/test_references.py` (신규)

**Interfaces:**
- Consumes: 없음
- Produces: `ReferenceDoc(id, source, title, content)`, `StaticReferenceAdapter(config_paths, attached_paths=(), root=None)`, `.load(ctx, requirement) -> list[ReferenceDoc]`, `Dependencies.references`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_references.py` 신규:

```python
"""배경 문서 공급. 파일 읽기는 조립 시점에 끝나야 한다."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from src.domain.models import BaseContext
from src.infrastructure.references import StaticReferenceAdapter
from tests.helpers import AS_OF

CTX = BaseContext(as_of=AS_OF, gbm="mx", factory="gumi")


def _write(root: Path, name: str, body: str) -> str:
    (root / name).write_text(body, encoding="utf-8")
    return name


class StaticReferenceAdapterTest(unittest.TestCase):
    """어댑터를 직접 테스트하는 예외다. 외부 의존이 없는 파일 읽기이고,
    '경로가 틀리면 부팅에서 멈춘다'가 이 어댑터의 핵심 동작이라 검증 가치가 크다.
    """

    def test_config_documents_get_sop_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _write(root, "sop.md", "# 정기점검 기준\n본문")
            b = _write(root, "kpi.md", "# KPI 정의\n본문")
            docs = asyncio.run(
                StaticReferenceAdapter([a, b], root=root).load(CTX, None)
            )
        self.assertEqual([d.id for d in docs], ["sop-01", "sop-02"])
        self.assertEqual(docs[0].title, "정기점검 기준")
        self.assertIn("본문", docs[0].content)

    def test_attached_documents_get_attach_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _write(root, "sop.md", "# 기준\n본문")
            b = _write(root, "plan.md", "# 정비 계획\n오늘 A라인 점검")
            docs = asyncio.run(
                StaticReferenceAdapter(
                    [a], attached_paths=[root / b], root=root
                ).load(CTX, None)
            )
        self.assertEqual([d.id for d in docs], ["sop-01", "attach-01"])
        self.assertEqual(docs[1].title, "정비 계획")

    def test_missing_file_fails_at_construction(self):
        """새벽 실행 중에 발견되는 것보다 부팅에서 멈추는 게 낫다."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError) as caught:
                StaticReferenceAdapter(["없는파일.md"], root=Path(tmp))
        self.assertIn("없는파일.md", str(caught.exception))

    def test_no_documents_is_fine(self):
        docs = asyncio.run(StaticReferenceAdapter([]).load(CTX, None))
        self.assertEqual(docs, [])

    def test_title_falls_back_to_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _write(root, "note.md", "제목 없는 본문")
            docs = asyncio.run(StaticReferenceAdapter([a], root=root).load(CTX, None))
        self.assertEqual(docs[0].title, "note")
```

**Step 1b: README에 예외 사유를 남긴다.** `tests/README.md`의 "아직 없는 것" 절에서 어댑터 항목을 다음으로 바꾼다:

```markdown
- **어댑터 테스트** — 의도적으로 뺐습니다. 이유와 대가는 [설계 문서 §11](../docs/superpowers/specs/2026-08-13-langgraph-report-template-design.md)에 있습니다.
  예외는 [test_references.py](test_references.py) 하나입니다 — `StaticReferenceAdapter`는 외부 의존 없이 로컬 파일만 읽고, "경로가 틀리면 부팅에서 멈춘다"가 그 어댑터의 핵심 동작이라 검증합니다
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_references.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.infrastructure.references'`

- [ ] **Step 3: 모델과 포트를 더한다**

`src/domain/models.py`의 `Requirement` 아래에 넣는다:

```python
class ReferenceDoc(BaseModel):
    """취합 단계가 참고하는 배경 문서.

    id를 두는 이유는 Record와 같다 — 서술이 이 문서를 인용하면 그 인용을
    코드로 대조할 수 있다. Record와 별도 타입인 이유는 성격이 다르기
    때문이다. Record는 as_of 시점의 관측값이고, 이쪽은 시점과 무관한
    배경 지식이다.
    """

    id: str
    source: str
    title: str
    content: str
```

`src/domain/ports.py`에 추가 (import에 `ReferenceDoc`, `Requirement` 보강):

```python
@runtime_checkable
class ReferencePort(Protocol):
    """취합 단계에 배경 문서를 공급한다.

    지금은 파일을 읽는 정적 어댑터뿐이지만, 질의에 맞춰 검색하는 어댑터로
    교체해도 취합 노드는 바뀌지 않는다. requirement를 인자로 받는 이유가
    그것이다 — 정적 어댑터에는 필요 없지만 검색 어댑터에는 필수다.
    """

    async def load(
        self, ctx: BaseContext, requirement: Requirement | None
    ) -> list[ReferenceDoc]: ...
```

- [ ] **Step 4: 어댑터를 만든다**

`src/infrastructure/references.py` 신규:

```python
"""배경 문서 어댑터 — 파일에서 읽어 ReferenceDoc으로 바꾼다.

파일 읽기는 **조립 시점에** 끝낸다. 경로가 틀렸으면 부팅에서 멈춰야지,
새벽 배치가 돌다가 알게 되면 곤란하다. MarkdownRenderer가 템플릿에 대해
하는 것과 같은 방침이다.
"""

from __future__ import annotations

from pathlib import Path

from src.constants import PROJECT_ROOT
from src.domain.models import BaseContext, ReferenceDoc, Requirement


def _title_of(text: str, path: Path) -> str:
    """첫 번째 마크다운 제목. 없으면 파일 이름."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.stem


def _read(path: Path, doc_id: str) -> ReferenceDoc:
    text = path.read_text(encoding="utf-8")
    return ReferenceDoc(
        id=doc_id, source=str(path), title=_title_of(text, path), content=text
    )


class StaticReferenceAdapter:
    """config에 적힌 운영 문서와 실행 시 첨부한 문서를 함께 다룬다.

    상대 경로는 저장소 루트 기준으로 푼다. 사내 공유 드라이브처럼 바깥에
    있는 문서는 절대 경로로 적으면 그대로 통한다.
    """

    def __init__(
        self,
        config_paths: list[str] | None = None,
        attached_paths: list[str | Path] | None = None,
        root: Path | None = None,
    ) -> None:
        base = root or PROJECT_ROOT
        docs: list[ReferenceDoc] = []
        for index, raw in enumerate(config_paths or [], start=1):
            path = Path(raw)
            docs.append(_read(path if path.is_absolute() else base / path, f"sop-{index:02d}"))
        for index, raw in enumerate(attached_paths or [], start=1):
            path = Path(raw)
            docs.append(
                _read(path if path.is_absolute() else base / path, f"attach-{index:02d}")
            )
        self._docs = docs

    async def load(
        self, ctx: BaseContext, requirement: Requirement | None
    ) -> list[ReferenceDoc]:
        return list(self._docs)
```

> `Path.read_text`가 없는 파일에 대해 `FileNotFoundError`를 던지므로 테스트가 요구하는 동작이 그대로 나온다. 예외 메시지에 경로가 들어간다.

- [ ] **Step 5: Dependencies에 배선한다**

`src/application/graph/state.py`의 `Dependencies`에 추가:

```python
    #: 취합 단계의 배경 문서. 나중에 검색 어댑터로 갈아끼울 자리다.
    references: Any = None
```

같은 파일 `ReportState`에 추가 (import에 `ReferenceDoc` 보강):

```python
    #: 취합에 실린 배경 문서. aggregate가 한 번만 쓰므로 리듀서가 없다.
    references: list[ReferenceDoc] = Field(default_factory=list)
```

`src/application/graph/builder.py`의 `build_dependencies` 시그니처와 반환을 고친다:

```python
def build_dependencies(
    cfg: DeployConfig,
    env: EnvConfig | None = None,
    replay: dict[str, str] | None = None,
    context_paths: list[str] | None = None,
) -> Dependencies:
```

`renderer = MarkdownRenderer(...)` 바로 아래에 넣는다:

```python
    # 경로가 틀렸으면 여기서 부팅이 멈춘다. 새벽 배치가 돌다가 알게 되는
    # 것보다 낫다.
    references = StaticReferenceAdapter(
        report_cfg.get("references", []), context_paths or []
    )
```

`return Dependencies(...)`에 `references=references,`를 더하고, import에 추가한다:

```python
from src.infrastructure.references import StaticReferenceAdapter
```

- [ ] **Step 6: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_references.py -v`
Expected: PASS (5건)

- [ ] **Step 7: 전체 회귀를 확인한다**

Run: `.venv/bin/python -m pytest`
Expected: PASS — `references`가 기본값 빈 목록이라 기존 config가 그대로 돈다.

- [ ] **Step 8: 커밋**

```bash
git add src/domain/models.py src/domain/ports.py src/infrastructure/references.py src/application/graph/state.py src/application/graph/builder.py tests/test_references.py
git commit -m "Supply background documents through a ReferencePort

Files are read at composition time so a wrong path stops the boot
rather than a 3am batch. The port takes the requirement it does not
yet need, so swapping in a search-backed adapter later does not move
the call site."
```

---

### Task 7: 취합에 배경 문서와 인용 검사 붙이기

`aggregate`는 `judge()`가 아니라 `narrate()`를 쓰므로 구조화된 근거 검증이 걸리지 않는다. 서술에서 대괄호 ID를 뽑아 대조하는 검사를 더한다.

**Files:**
- Modify: `src/application/graph/aggregate.py:17-55` (`make_aggregate`)
- Test: `tests/test_aggregate_references.py` (신규)

**Interfaces:**
- Consumes: `Dependencies.references`, `ReferenceDoc` (Task 6), `ReportState.requirement` (Task 3)
- Produces: `_unknown_citations(text: str, allowed: set[str]) -> list[str]`, `aggregate`가 `references`와 `guardrail_drops`를 State에 올린다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_aggregate_references.py` 신규:

```python
"""취합 단계의 배경 문서 반영과 인용 사후 검사.

취합 서술은 자유 텍스트라 judge()의 근거 강제가 걸리지 않는다. 그래서
대괄호 인용을 코드로 대조한다. LLM 없이 도는 결정론적 검사다.
"""

from __future__ import annotations

import asyncio
import unittest

from src.application.graph.aggregate import _unknown_citations, make_aggregate
from src.application.graph.state import Dependencies, ReportState
from src.domain.models import (
    BaseContext,
    Judgement,
    ReferenceDoc,
    ReportSection,
    Requirement,
    Severity,
)
from src.infrastructure.llm import FakeLLMAdapter
from tests.helpers import AS_OF

CTX = BaseContext(as_of=AS_OF, gbm="mx", factory="gumi")


class Refs:
    """참고 문서 포트의 가짜 구현. 파일을 읽지 않는다."""

    def __init__(self, docs):
        self.docs = docs

    async def load(self, ctx, requirement):
        return list(self.docs)


class Cites(FakeLLMAdapter):
    """정해진 문장을 그대로 서술로 내놓는다. 인용 검사를 겨냥한 것이다."""

    text = ""

    async def _complete(self, prompt):
        return self.text


def _state(**kw):
    section = ReportSection(
        key="kpi.check",
        title="KPI 점검",
        severity=Severity.WARNING,
        judgements=[
            Judgement(subject="수율 미달", severity=Severity.WARNING, reasoning="…")
        ],
    )
    return ReportState(ctx=CTX, sections=[section], **kw)


def _run(docs=(), llm=None, state=None):
    deps = Dependencies(
        llm=llm or FakeLLMAdapter(model="fake-local", seed="t"), references=Refs(docs)
    )
    return asyncio.run(make_aggregate(deps)(state or _state()))


def _citing(text):
    llm = Cites(model="fake-local")
    llm.text = text
    return llm


class CitationCheckTest(unittest.TestCase):
    def test_flags_only_unlisted_ids(self):
        allowed = {"kpi.check", "sop-01"}
        self.assertEqual(_unknown_citations("[kpi.check] 와 [sop-01] 참고", allowed), [])
        self.assertEqual(_unknown_citations("[sop-09] 에 따르면", allowed), ["sop-09"])

    def test_unknown_citation_is_recorded_as_guardrail_drop(self):
        out = _run(llm=_citing("[sop-99] 에 따르면 조치가 필요합니다."))
        self.assertTrue(any("sop-99" in d for d in out["guardrail_drops"]))

    def test_valid_citation_is_not_flagged(self):
        docs = [ReferenceDoc(id="sop-01", source="s", title="기준", content="본문")]
        out = _run(docs=docs, llm=_citing("[sop-01] 기준에 비추어 정상입니다."))
        self.assertEqual(out["guardrail_drops"], [])

    def test_narrative_is_kept_even_when_citation_is_unknown(self):
        """취합 서술은 판정이 아니라 요약이라 폐기 대상이 아니다."""
        out = _run(llm=_citing("[sop-99] 에 따르면 조치가 필요합니다."))
        self.assertIn("조치가 필요합니다", out["overall"].narrative)


class AggregateReferenceTest(unittest.TestCase):
    def test_documents_reach_the_prompt_with_ids(self):
        docs = [
            ReferenceDoc(
                id="sop-01", source="s", title="정기점검 기준", content="A라인 점검 중"
            )
        ]
        prompt = _run(docs=docs)["traces"][0].prompt
        self.assertIn("[sop-01]", prompt)
        self.assertIn("A라인 점검 중", prompt)

    def test_documents_land_in_state(self):
        docs = [ReferenceDoc(id="sop-01", source="s", title="기준", content="본문")]
        out = _run(docs=docs)
        self.assertEqual([d.id for d in out["references"]], ["sop-01"])

    def test_focus_reaches_the_prompt(self):
        out = _run(
            state=_state(requirement=Requirement(query="재고", focus=["재고", "소진"]))
        )
        self.assertIn("재고, 소진", out["traces"][0].prompt)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_aggregate_references.py -v`
Expected: FAIL — `ImportError: cannot import name '_unknown_citations'`

- [ ] **Step 3: 구현한다**

`src/application/graph/aggregate.py` 상단 import에 `import re`를 더하고, `AGGREGATE_NODE` 아래에 검사기를 둔다:

```python
#: 대괄호 인용. 소문자·숫자로 시작하는 토큰만 본다. 한글 대괄호 강조와
#: 섞이지 않게 하려는 것이다 (예: "[심각]"은 인용이 아니다).
_CITE = re.compile(r"\[([a-z0-9][\w.\-]*)\]")


def _unknown_citations(text: str, allowed: set[str]) -> list[str]:
    """서술이 인용한 ID 중 실재하지 않는 것.

    aggregate는 narrate()를 쓰므로 judge()의 근거 강제가 걸리지 않는다.
    그래서 사후에 대조한다. LLM 없이 도는 결정론적 검사라 항상 켠다.
    """
    return sorted({m for m in _CITE.findall(text) if m not in allowed})
```

`aggregate` 본문을 교체한다. `facts` 계산까지는 그대로 두고, 프롬프트 구성부터 반환까지를 다음으로 바꾼다:

```python
        docs = await deps.references.load(state.ctx, state.requirement) \
            if deps.references is not None else []

        prompt = (
            "아래는 각 분석의 판정 결과입니다. 심각 "
            f"{counts['critical']}건, 경고 {counts['warning']}건입니다.\n"
            "운영 담당자가 오늘 무엇부터 봐야 할지 2~3문장으로 정리하세요. "
            "숫자를 새로 만들지 말고 아래 값만 인용하세요.\n" + facts
        )
        if docs:
            # 대괄호 ID를 함께 준다. 서술이 이것을 인용하면 사후 대조가 된다.
            body = "\n\n".join(f"[{d.id}] {d.title}\n{d.content}" for d in docs)
            prompt += (
                "\n\n참고 문서입니다. 판정을 해석할 때 함께 고려하고, 인용할 때는 "
                f"대괄호 안의 id를 그대로 쓰세요.\n{body}"
            )
        if state.requirement and state.requirement.focus:
            prompt += (
                "\n\n사용자가 특히 알고자 하는 것: "
                f"{', '.join(state.requirement.focus)}"
            )

        narrative = await deps.llm.narrate(AGGREGATE_NODE, prompt)

        # 서술 자체는 지우지 않는다. 문장 중간을 잘라내면 읽을 수 없는 글이
        # 되고, 취합 서술은 판정이 아니라 요약이라 폐기 대상이 아니다.
        allowed = {s.key for s in state.sections} | {d.id for d in docs}
        unknown = _unknown_citations(narrative, allowed)
        drops = deps.llm.drain_guardrail_drops()
        if unknown:
            drops.append(
                f"{AGGREGATE_NODE}: 서술이 인용한 {unknown!r}이 실제 자료에 없습니다"
            )

        return {
            "overall": OverallSummary(
                narrative=narrative, counts=counts, top_issues=top_issues
            ),
            "references": docs,
            "traces": deps.llm.drain_traces(),
            "guardrail_drops": drops,
        }
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_aggregate_references.py -v`
Expected: PASS (7건)

- [ ] **Step 5: 전체 회귀를 확인한다**

Run: `.venv/bin/python -m pytest`
Expected: PASS — `deps.references`가 `None`인 기존 테스트 경로도 `if deps.references is not None` 덕에 돈다.

- [ ] **Step 6: 커밋**

```bash
git add src/application/graph/aggregate.py tests/test_aggregate_references.py
git commit -m "Feed reference documents into aggregate and check citations

The overall summary is free prose, so evidence enforcement cannot
apply. Bracketed ids in the narrative are matched against section keys
and reference ids instead, and a mismatch is recorded rather than
edited out — cutting mid-sentence would leave unreadable prose."
```

---

### Task 8: 브로드캐스트 발송 제외와 질의별 멱등키

임시 질의 결과가 정규 수신자 전원에게 나가면 안 된다. 채널 이름을 노드가 알게 하는 대신 채널에 표시를 둔다.

**Files:**
- Modify: `src/infrastructure/delivery.py:22-40`
- Modify: `src/domain/ports.py:86-92` (`DeliveryPort`)
- Modify: `src/application/graph/aggregate.py:68-85` (`make_deliver`)
- Test: `tests/test_delivery_scope.py` (신규)

**Interfaces:**
- Consumes: `query_digest` (Task 1)
- Produces: `FileDelivery.broadcast = False`, `MailDelivery.broadcast = True`, 질의가 있으면 멱등키에 `_q<digest>`가 붙는다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_delivery_scope.py` 신규:

```python
"""질의 실행의 발송 범위와 멱등키.

임시 질의 결과가 정규 리포트 파일을 덮어쓰거나 정규 수신자에게 나가면
안 된다.
"""

from __future__ import annotations

import asyncio
import unittest

from src.application.graph.aggregate import make_deliver
from src.application.graph.state import Dependencies, ReportState
from src.domain.models import BaseContext
from src.infrastructure.delivery import FileDelivery, MailDelivery
from tests.helpers import AS_OF

SCHEDULED_KEY = "mx_gumi_20260813T0800"


class Spy:
    """발송 채널의 가짜 구현. 실제 I/O 없이 받은 키만 기록한다."""

    def __init__(self, channel, broadcast):
        self.channel = channel
        self.broadcast = broadcast
        self.keys = []

    async def deliver(self, key, content):
        self.keys.append(key)
        return f"{self.channel}://{key}"


def _run(query, channels):
    deps = Dependencies(deliveries=channels)
    ctx = BaseContext(as_of=AS_OF, gbm="mx", factory="gumi", query=query)
    return asyncio.run(make_deliver(deps)(ReportState(ctx=ctx, rendered="본문")))


class DeliveryScopeTest(unittest.TestCase):
    def test_scheduled_run_uses_every_channel(self):
        file_ch, mail_ch = Spy("file", False), Spy("mail", True)
        out = _run(None, [file_ch, mail_ch])
        self.assertEqual(len(out["delivered"]), 2)
        self.assertEqual(file_ch.keys, [SCHEDULED_KEY])
        self.assertEqual(mail_ch.keys, [SCHEDULED_KEY])

    def test_query_run_skips_broadcast_channels(self):
        file_ch, mail_ch = Spy("file", False), Spy("mail", True)
        out = _run("재고만", [file_ch, mail_ch])
        self.assertEqual([d.channel for d in out["delivered"]], ["file"])
        self.assertEqual(mail_ch.keys, [])

    def test_query_run_uses_a_distinct_idempotency_key(self):
        """정규 리포트 파일을 덮어쓰면 안 된다."""
        file_ch = Spy("file", False)
        _run("재고만", [file_ch])
        self.assertTrue(file_ch.keys[0].startswith(f"{SCHEDULED_KEY}_q"))
        self.assertNotEqual(file_ch.keys[0], SCHEDULED_KEY)

    def test_same_query_reuses_the_same_key(self):
        a, b = Spy("file", False), Spy("file", False)
        _run("재고만", [a])
        _run("재고만", [b])
        self.assertEqual(a.keys, b.keys)

    def test_shipped_channels_declare_broadcast(self):
        self.assertFalse(FileDelivery.broadcast)
        self.assertTrue(MailDelivery.broadcast)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_delivery_scope.py -v`
Expected: FAIL — `AttributeError: type object 'FileDelivery' has no attribute 'broadcast'`

- [ ] **Step 3: 채널에 표시를 단다**

`src/infrastructure/delivery.py`:

```python
class FileDelivery:
    """md 파일로 저장."""

    channel = "file"
    #: 정해진 수신자에게 밀어내는 채널이 아니다. 질의 실행에서도 남긴다.
    broadcast = False
```

```python
class MailDelivery:
    """메일 발송. 실제 SMTP 호출은 주석 처리했다."""

    channel = "mail"
    #: 정규 수신자 전원에게 나간다. 사람이 임시로 던진 질의 결과는 보내지 않는다.
    broadcast = True
```

`src/domain/ports.py`의 `DeliveryPort`:

```python
@runtime_checkable
class DeliveryPort(Protocol):
    """발송 채널. 멱등키로 중복을 막는다."""

    channel: str
    #: 정해진 수신자에게 밀어내는 채널인가. 질의 실행에서는 건너뛴다.
    broadcast: bool

    async def deliver(self, key: str, content: str) -> str: ...
```

- [ ] **Step 4: 발송 노드를 고친다**

`src/application/graph/aggregate.py` import에 `from src.infrastructure.checkpoint import query_digest`를 더하고, 파일 상단에 `import logging` / `logger = logging.getLogger(__name__)`이 없으면 추가한다. `make_deliver`를 교체:

```python
def make_deliver(deps: Dependencies):
    async def deliver(state: ReportState) -> dict:
        ctx = state.ctx
        # 멱등키. 재개하면 State와 함께 복원되므로 중복 발송을 막는다.
        # 질의가 범위를 바꾸므로 키에도 들어가야 한다 — 빠뜨리면 임시 질의
        # 실행이 정규 리포트 파일을 덮어쓴다.
        key = f"{ctx.gbm}_{ctx.factory}_{ctx.as_of:%Y%m%dT%H%M}"
        digest = query_digest(ctx.query)
        if digest:
            key += f"_q{digest}"
        already = {d.channel for d in state.delivered}

        records = []
        for channel in deps.deliveries:
            if channel.channel in already:
                continue
            # 채널 이름을 여기서 비교하지 않는다. 나중에 Slack이 붙어도
            # 같은 규칙이 그대로 적용되어야 한다.
            if ctx.query and getattr(channel, "broadcast", False):
                logger.info(
                    "channel=%s 질의 실행이라 발송을 건너뜁니다", channel.channel
                )
                continue
            target = await channel.deliver(key, state.rendered or "")
            records.append(
                DeliveryRecord(channel=channel.channel, key=key, target=target)
            )
        return {"delivered": records}

    return deliver
```

- [ ] **Step 5: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_delivery_scope.py -v`
Expected: PASS (5건)

- [ ] **Step 6: 전체 회귀를 확인한다**

Run: `.venv/bin/python -m pytest`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add src/infrastructure/delivery.py src/domain/ports.py src/application/graph/aggregate.py tests/test_delivery_scope.py
git commit -m "Keep ad-hoc query runs off broadcast channels

The delivery node checks a flag on the channel rather than comparing
channel names, so a future Slack channel inherits the rule for free.
The query digest also joins the idempotency key so a query run cannot
overwrite the scheduled report or mark it as already sent."
```

---

### Task 9: 리포트에 질의 범위 표기

선택 실행과 전체 실행을 읽는 사람이 구분할 수 있어야 한다. 머리말에 한 줄만 더한다.

**Files:**
- Modify: `src/presentation/renderers.py:89-105` (`render`)
- Modify: `src/presentation/templates/report.md` (`document` 블록)
- Modify: `src/application/graph/aggregate.py` (`make_render`가 payload에 `requirement`를 싣는다)
- Test: `tests/test_renderer.py` (추가)

**Interfaces:**
- Consumes: `ReportState.requirement` (Task 3)
- Produces: 템플릿 변수 `${scope}` — 전체 실행이면 빈 문자열

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_renderer.py` 끝에 추가:

파일 상단 import에 `Requirement`를 보강한다 (`BaseContext`, `CTX`, `unittest`는 이미 있다).

```python
class ScopeLineTest(unittest.TestCase):
    """선택 실행과 전체 실행을 읽는 사람이 구분할 수 있어야 한다."""

    def _render(self, payload):
        return MarkdownRenderer().render(CTX, payload)

    def test_absent_for_full_scope(self):
        out = self._render(
            {
                "sections": [],
                "overall": None,
                "requirement": Requirement(
                    selected=["kpi.check"], is_full_scope=True
                ),
            }
        )
        self.assertNotIn("질의", out)

    def test_shows_query_and_selection(self):
        out = self._render(
            {
                "sections": [],
                "overall": None,
                "requirement": Requirement(
                    query="재고만 보여줘",
                    selected=["material.stock"],
                    is_full_scope=False,
                ),
            }
        )
        self.assertIn("재고만 보여줘", out)
        self.assertIn("material.stock", out)

    def test_absent_without_requirement(self):
        """기존 호출부가 requirement 없이 불러도 죽지 않는다."""
        out = self._render({"sections": [], "overall": None})
        self.assertNotIn("질의", out)
        self.assertNotIn("${scope}", out)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_renderer.py -k scope -v`
Expected: FAIL — `test_scope_line_shows_query_and_selection`에서 `assert "재고만 보여줘" in out`

- [ ] **Step 3: 렌더러를 고친다**

`src/presentation/renderers.py`의 `render`를 교체:

```python
    def render(self, ctx: BaseContext, payload: dict) -> str:
        sections: list[ReportSection] = payload["sections"]
        overall = payload.get("overall")

        document = _fill(
            self.blocks["document"],
            {
                "gbm": ctx.gbm.upper(),
                "factory": ctx.factory.upper(),
                "as_of": ctx.as_of.isoformat(),
                "section_count": len(sections),
                "scope": self._render_scope(payload.get("requirement")),
                "overall": self._render_overall(overall),
                "sections": self._render_sections(sections),
            },
        )
        # 비어버린 블록(요약 없음, 지표 없음)이 남긴 여백을 정리한다.
        return BLANK_RUN.sub("\n\n", document).strip() + "\n"

    @staticmethod
    def _render_scope(requirement) -> str:
        """질의로 범위를 좁힌 리포트임을 밝힌다.

        전체 실행이면 아무것도 쓰지 않는다. 스케줄러 리포트의 모양이
        바뀌지 않아야 하기 때문이다.
        """
        if requirement is None or requirement.is_full_scope:
            return ""
        picked = ", ".join(f"`{name}`" for name in requirement.selected)
        return f"\n- 질의: {requirement.query}\n- 선택된 분석: {picked}"
```

`src/presentation/templates/report.md`의 `document` 블록에 `${scope}`를 넣는다:

```markdown
<!-- block: document -->
# 운영 상태 리포트 — ${gbm} / ${factory}

- 기준 시각: `${as_of}`
- 분석 항목: ${section_count}건${scope}
${overall}${sections}
```

같은 파일 머리말 주석의 변수 목록에 `scope`를 더한다:

```
       document       : gbm factory as_of section_count scope overall sections
```

`src/application/graph/aggregate.py`의 `make_render`가 payload에 실어 보낸다:

```python
def make_render(deps: Dependencies):
    async def render(state: ReportState) -> dict:
        content = deps.renderer.render(
            state.ctx,
            {
                "sections": state.sections,
                "overall": state.overall,
                "requirement": state.requirement,
            },
        )
        return {"rendered": content}

    return render
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_renderer.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/presentation/renderers.py src/presentation/templates/report.md src/application/graph/aggregate.py tests/test_renderer.py
git commit -m "Mark narrowed reports with the query and chosen analyses

A full-scope run renders nothing extra, so the scheduled report keeps
its exact current shape."
```

---

### Task 10: CLI와 유스케이스 배선

`--query`와 `--context`를 받아 그래프까지 잇는다. 이 태스크가 끝나면 기능이 실제로 동작한다.

**Files:**
- Modify: `src/config/loader.py:66` (`DeployConfig.root` 속성)
- Modify: `src/application/usecase.py:59-110` (`run_report`)
- Modify: `src/presentation/cli.py:58-81` (인자), `:107-147` (`_run`)
- Test: `tests/test_query_end_to_end.py` (신규)

**Interfaces:**
- Consumes: 앞의 모든 태스크
- Produces: `run_report(..., query: str | None = None, context_paths: list[str] | None = None)`, CLI `--query`, `--context`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_query_end_to_end.py` 신규:

```python
"""질의 경로 전체. CLI가 아니라 유스케이스를 부른다."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from src.application.usecase import run_report
from tests.helpers import AS_OF, FACTORY, GBM, temp_config, with_subgraph_patch

SCHEDULED_THREAD = "mx:gumi:20260813T0800"


def _run(**kw):
    cfg = with_subgraph_patch(lambda c: None)
    with temp_config(gbm=cfg) as deploy:
        return asyncio.run(
            run_report(GBM, FACTORY, AS_OF, config_root=deploy.root, **kw)
        )


class QueryEndToEndTest(unittest.TestCase):
    def test_scheduler_path_is_unchanged(self):
        run = _run()
        self.assertEqual(run.thread_id, SCHEDULED_THREAD)
        self.assertTrue(run.state["requirement"].is_full_scope)

    def test_query_narrows_the_report(self):
        run = _run(query="material.stock 만")
        self.assertEqual({s.key for s in run.sections}, {"material.stock"})
        self.assertTrue(run.thread_id.startswith(f"{SCHEDULED_THREAD}:q"))
        self.assertIn("material.stock", run.rendered)

    def test_context_documents_reach_the_report_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.md"
            path.write_text("# 정비 계획\n오늘 A라인 점검", encoding="utf-8")
            run = _run(query="material.stock 만", context_paths=[str(path)])
        self.assertEqual([d.id for d in run.state["references"]], ["attach-01"])
        self.assertEqual(run.state["references"][0].title, "정비 계획")
```

`DeployConfig`는 config 경로를 `self._root`(private)로만 갖고 있다. 테스트가 private에 손대지 않도록 읽기 전용 속성을 하나 연다. `src/config/loader.py:66`의 `self.data = self._load_merged()` 아래에 추가:

```python
    @property
    def root(self) -> Path:
        """이 설정을 읽어온 config 디렉터리. 진단과 테스트가 쓴다."""
        return self._root
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_query_end_to_end.py -v`
Expected: FAIL — `TypeError: run_report() got an unexpected keyword argument 'query'`

- [ ] **Step 3: 유스케이스를 고친다**

`src/application/usecase.py`의 `run_report` 시그니처와 본문:

```python
async def run_report(
    gbm: str,
    factory: str,
    as_of: datetime,
    *,
    env: EnvConfig | None = None,
    replay: dict[str, str] | None = None,
    on_node: Any = None,
    config_root: Path | None = None,
    query: str | None = None,
    context_paths: list[str] | None = None,
) -> ReportRun:
```

본문에서 세 곳을 고친다:

```python
    deps = build_dependencies(cfg, env, replay=replay, context_paths=context_paths)
    graph = build_graph(cfg, deps, env, replay=replay)

    ctx = BaseContext(as_of=as_of, gbm=gbm, factory=factory, query=query)
    thread_id = thread_id_for(gbm, factory, as_of, query)
```

- [ ] **Step 4: CLI 인자를 더한다**

`src/presentation/cli.py`의 `run` 파서에 추가:

```python
    run.add_argument(
        "--query",
        help="질의로 리포트 범위와 서술 초점을 좁힌다. 생략하면 전체 분석.",
    )
    run.add_argument(
        "--context",
        action="append",
        metavar="PATH",
        help="취합에 함께 볼 참고 문서. 여러 번 쓸 수 있다.",
    )
```

`_run`에서 락 키와 호출을 고친다:

```python
    print(f"▶ {gbm}/{factory} · as_of={as_of.isoformat(timespec='seconds')}")
    if args.query:
        print(f"  질의: {args.query}")
    if replay:
        print(f"  replay: {len(replay)}건의 저장된 응답을 재생합니다")

    # 중복 실행 방지. 질의가 범위를 바꾸므로 락 키에도 넣는다 — 그래야
    # 배치가 도는 중에도 사람이 다른 질의를 던질 수 있다.
    lock = RunLock(thread_id_for(gbm, factory, as_of, args.query), LOCK_ROOT)
```

```python
        run = await run_report(
            gbm,
            factory,
            as_of,
            env=env,
            replay=replay,
            on_node=(lambda node: print(f"  · {node}")) if args.stream else None,
            query=args.query,
            context_paths=args.context,
        )
```

- [ ] **Step 5: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_query_end_to_end.py -v`
Expected: PASS (3건)

- [ ] **Step 6: 전체 테스트를 돌린다**

Run: `.venv/bin/python -m pytest`
Expected: PASS (전부)

- [ ] **Step 7: 실제로 돌려서 눈으로 확인한다**

```bash
.venv/bin/python -m src run --gbm mx --factory gumi --stream
.venv/bin/python -m src run --gbm mx --factory gumi --query "material.stock 상황만" --stream
```

Expected:
- 첫 번째 — 서브그래프 6개가 모두 실행되고 리포트에 질의 줄이 없다
- 두 번째 — `analyze_query` 뒤에 `material.stock`만 실행되고, 리포트 머리말에 질의와 선택된 분석이 표시되며, `⚠ 가드레일:` 줄에 Fake 어댑터가 섞은 `nonexistent.analysis` 폐기 기록이 보인다

- [ ] **Step 8: config에 참고 문서 예시를 남긴다**

`config/gbm/mx.json`의 `report` 블록에 빈 목록을 추가해 자리를 보여준다:

```json
  "report": {
    "template": "report.md",
    "references": []
  },
```

`report` 블록이 없으면 `delivery` 옆에 새로 만든다. 값을 채우지 않는 이유는 저장소에 SOP 문서가 없기 때문이며, 경로를 적으면 부팅에서 바로 실패한다.

- [ ] **Step 9: 커밋**

```bash
git add src/application/usecase.py src/presentation/cli.py config/gbm/mx.json tests/test_query_end_to_end.py
git commit -m "Wire --query and --context through to the graph

CLI and scheduler still call the same run_report. The run lock keys on
the query too, so an ad-hoc question is not blocked by the batch that
happens to be running."
```

---

## 자체 검토

**스펙 대조** — 설계 문서의 각 절이 어느 태스크에 들어갔는지.

| 스펙 절 | 태스크 |
|---|---|
| 4-1 `BaseContext.query` | 1 |
| 4-2 `Requirement` | 2 |
| 4-3 `ReferenceDoc` | 6 |
| 5 실행 식별자 (`thread_id`, 멱등키) | 1, 8 |
| 6 `analyze_query` 노드 | 3 |
| 7 선택 가드레일 | 2 |
| 8 조건부 fan-out | 4 |
| 9 `ReferencePort` | 6 |
| 10 인용 사후 검사 | 7 |
| 11 `Requirement`·참고 문서 전달 | 3, 5, 7 |
| 12 발송 규칙 | 8 |
| 13 config·CLI | 10 |
| 15 테스트 전략 | 각 태스크에 분산 |

빠진 항목 없음. 스펙 16절의 미결 두 가지는 이 계획에서 결정했다.

- **참고 문서 경로** — 상대 경로는 `PROJECT_ROOT` 기준, 절대 경로는 그대로 통과(Task 6). 사내 공유 드라이브 경로를 그대로 적을 수 있다.
- **`is_full_scope` 표기** — 전체 실행이면 아무것도 안 쓰고, 선택 실행이면 머리말에 질의와 선택 목록 두 줄(Task 9). 스케줄러 리포트 모양이 안 바뀐다.

**타입 일관성** — `Requirement`의 필드명(`query` `focus` `selected` `rationale` `dropped` `is_full_scope`)이 Task 2 정의부터 Task 9 렌더링까지 동일하다. `query_digest`는 Task 1에서 정의해 Task 8에서만 재사용한다. `ReferenceDoc.id`는 Task 6 생성, Task 7 대조로 이어진다. `broadcast`는 Task 8에서 클래스 속성과 프로토콜에 함께 선언한다.

**위험 지점** — 기존 호출부를 깨뜨리는 변경이 없다. 모든 신규 State 필드에 기본값이 있고, `build_dependencies`와 `run_report`의 새 인자도 기본값이 있다. `DeployConfig.root`는 기존 `_root`를 읽기 전용으로 노출하는 것뿐이라 동작이 바뀌지 않는다.

**미검증으로 남은 것 없음** — 설계 문서가 유일한 가정으로 표시했던 "조건부 fan-out에서 취합 barrier가 도는가"는 계획 작성 전에 LangGraph 1.2.11로 확인했다(Global Constraints 참조). 확인 결과가 반대였다면 Task 4의 구조가 달라졌을 것이다.

**회귀 방어선** — Task 1·4·8·9·10에 "질의 없는 경로가 그대로인가"를 확인하는 테스트가 각각 들어 있다. 특히 Task 4 Step 5의 `tests/test_graph_behaviour.py` 통과가 핵심 신호다.

**실행 전 규약 대조에서 고친 것** — 초안은 테스트를 pytest 함수형으로 썼는데, 이 저장소는 9개 파일 전부가 `unittest.TestCase`이고 `tests/README.md`가 "pytest 없이도 돕니다"를 보증한다. 그대로 갔으면 `python -m unittest discover`가 신규 테스트를 하나도 수집하지 못했을 것이다. 전 태스크의 테스트를 `unittest`로 바꿨고, Global Constraints에 두 실행 명령이 **모두** 통과해야 한다고 못박았다. 또한 `tests/README.md`가 어댑터 테스트를 의도적으로 제외하고 있어, Task 6이 그 예외임을 README에 남기는 단계를 추가했다.
