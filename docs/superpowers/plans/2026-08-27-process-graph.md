# process 중첩 그래프 (B-1) — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 서브그래프의 `process`를 메서드 하나에서 중첩 그래프로 확장해, 판정 결과에 따라 데이터를 추가 조회하고 다시 판정하는 흐름을 실패 격리와 재현성을 유지한 채 표현한다.

**Architecture:** `BaseSubgraph.build_process()`가 `None`이 아니면 컴파일된 그래프를 `process` 슬롯에 꽂는다. 그 그래프는 `fetch → compute → judge → decide_next`이고, `decide_next`가 고른 probe가 돌면 `judge`로 되돌아온다. LLM은 **목적지 이름만** 고르고, 멈출 시점과 각 probe가 무엇을 조회할지는 코드가 쥔다. `guarded()`는 바깥에 그대로 남아 분석 단위 실패 격리를 유지한다.

**Tech Stack:** Python 3, LangGraph 1.2.11, LangChain Core 1.5.4, Pydantic 2.13.4, pytest 9.1.1

## Global Constraints

- **`build_process()`를 구현하지 않은 서브그래프는 동작이 문자열 단위로 동일해야 한다.** 기존 6개가 전부 그 경로이며, `tests/test_graph_behaviour.py`가 손대지 않고 통과하는 것이 핵심 신호다.
- **테스트는 `unittest.TestCase`로 쓴다.** 기존 파일이 전부 그렇고 `tests/README.md`가 "pytest 없이도 돕니다"를 보증한다. 함수형 pytest 테스트, `import pytest`, `pytest.raises` 금지 — 예외 검사는 `with self.assertRaises(...)`.
- 실행 명령: `.venv/bin/python -m pytest` **와** `.venv/bin/python -m unittest discover -s tests -t .` **둘 다** 통과해야 한다. 앱은 `.venv/bin/python -m src`.
- **멈출 시점은 LLM에게 묻지 않는다.** 상한 도달과 후보 소진은 코드가 먼저 검사하고, 넘었으면 LLM 호출 없이 끝낸다.
- **조회 대상은 `required_kinds`를 벗어날 수 없다.** probe의 `kinds`가 그 안에 있는지 조립 시점에 검사한다.
- config를 보는 곳은 Composition Root(`builder.py`)뿐이다. 노드는 config 객체를 들고 다니지 않는다.
- `as_of`는 밖에서 주입된다. 노드 안에서 `datetime.now()`를 부르지 않는다.
- 주석과 사용자 노출 문자열은 한국어. 주석은 "왜"를 적고 자명한 "무엇"은 적지 않는다.
- `FakeLLMAdapter`는 가드레일이 도는지 LLM 없이 보이려고 **의도적으로 잘못된 값을 섞는** 기존 규약을 따른다 (`_judge_raw`의 `::hallucinated`, `_plan_raw`의 `nonexistent.analysis`).

**검증 완료된 전제** (최소 그래프로 LangGraph 1.2.11에서 확인):

1. 중첩 그래프의 `ainvoke(state)`는 **입력 필드까지 포함한 전체 상태를 dict로** 돌려준다. 그래서 바깥으로 올릴 필드를 골라야 한다.
2. `add_edge("probe_x", "judge")`로 앞 노드로 되돌아가는 재귀가 정상 동작한다.
3. 리듀서 없이 `{"probe_records": [*state.probe_records, new]}`로 누적하면 정확히 라운드 수만큼 쌓인다.

**스펙에서 확정한 모호점:** 스펙 4절은 `compute`가 "지표 + 임계치 판정"을 만든다고 했으나 7절은 `judge`가 판정을 통째로 덮어쓴다고 한다. 그대로면 임계치 판정이 사라진다. **이 계획에서는 `compute` → 지표만, `judge` → 판정 전부(임계치 + LLM)로 확정한다.** 임계치 판정은 base records만 보므로 매 라운드 다시 계산해도 결과가 같고 비용도 없다.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `src/application/subgraphs/base.py` | 슬롯 일반화(`build_process`, `_run_nested`), `SubgraphState` 필드, `SubgraphConfig.max_probe_rounds`, `generate_output`의 drops 병합 |
| `src/application/subgraphs/process_graph.py` | **신규** — `Probe`, `ProcessGraph`, `probe_guarded`. 중첩 그래프 조립과 분기가 여기 모인다 |
| `src/domain/models.py` | `ProbeDecision` |
| `src/domain/ports.py` | `LLMPort.decide()` |
| `src/infrastructure/llm.py` | `decide()`, `_decide_raw()`, 선택 허용목록 검사 |
| `src/application/subgraphs/kpi/check.py` | `fetch`/`compute`/`judge` 분리, `build_process()`, probe 두 개, `required_kinds` 확장 |
| `config/gbm/mx.json` | `kpi.check.max_probe_rounds` |
| `tests/test_process_graph.py` | **신규** — 슬롯·상한·허용목록·실패 격리 |
| `tests/test_kpi_probes.py` | **신규** — `kpi.check` 적용 결과 |

---

### Task 1: 상태 필드와 process 슬롯 일반화

중첩 그래프를 꽂을 자리를 만든다. 이 태스크만으로는 어떤 서브그래프도 동작이 바뀌지 않아야 한다.

**Files:**
- Modify: `src/application/subgraphs/base.py`
- Modify: `src/application/nodes/outputs.py` (`no_llm`도 drops를 합친다)
- Test: `tests/test_process_graph.py` (신규)

**Interfaces:**
- Produces: `BaseSubgraph.build_process() -> Any | None` (기본 `None`), `_run_nested(compiled) -> node`, `SubgraphState.probe_records/probe_rounds/probed`, `SubgraphConfig.max_probe_rounds: int = 0`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_process_graph.py` 신규:

```python
"""process 슬롯 일반화.

build_process()를 구현하지 않으면 기존 process 메서드를 그대로 쓴다.
기존 6개 서브그래프가 전부 그 경로이므로, 이 변경으로 동작이 바뀌면 안 된다.
"""

from __future__ import annotations

import asyncio
import unittest

from langgraph.graph import END, START, StateGraph

from src.application.graph.state import Dependencies
from src.application.subgraphs.base import (
    BaseSubgraph,
    SubgraphConfig,
    SubgraphState,
)
from src.domain.models import Metric, Record, SnapshotContext
from src.infrastructure.llm import FakeLLMAdapter
from tests.helpers import AS_OF

CTX = SnapshotContext(as_of=AS_OF, gbm="mx", factory="gumi")


class MethodSubgraph(BaseSubgraph):
    """build_process를 구현하지 않는 기존 방식."""

    registry_name = "test.method"
    title = "메서드 방식"

    async def process(self, state: SubgraphState) -> dict:
        return {"records": [Record(id="r1")], "metrics": [Metric(name="m", value=1)]}


class NestedSubgraph(BaseSubgraph):
    """build_process로 중첩 그래프를 꽂는 방식."""

    registry_name = "test.nested"
    title = "중첩 그래프 방식"

    def build_process(self):
        async def step(state: SubgraphState) -> dict:
            return {
                "records": [Record(id="r1")],
                "metrics": [Metric(name="m", value=1)],
            }

        g = StateGraph(SubgraphState)
        g.add_node("step", step)
        g.add_edge(START, "step")
        g.add_edge("step", END)
        return g.compile()


def deps():
    """generate_output이 어댑터를 무조건 drain하므로 llm 없이는 못 돈다."""
    return Dependencies(llm=FakeLLMAdapter(model="fake-local", seed="t"))


def run(subgraph_cls):
    sub = subgraph_cls(SubgraphConfig(enabled=True), deps())
    compiled = sub.compile()
    return asyncio.run(compiled.ainvoke(SubgraphState(ctx=CTX, scoped=CTX)))


class ProcessSlotTest(unittest.TestCase):
    def test_default_uses_the_process_method(self):
        self.assertIsNone(MethodSubgraph(SubgraphConfig(), deps()).build_process())

    def test_nested_graph_produces_the_same_shape(self):
        """두 방식의 결과가 같아야 슬롯 교체가 안전하다."""
        from_method = run(MethodSubgraph)
        from_nested = run(NestedSubgraph)
        self.assertEqual(
            [r.id for r in from_method["records"]],
            [r.id for r in from_nested["records"]],
        )
        self.assertEqual(
            [m.name for m in from_method["metrics"]],
            [m.name for m in from_nested["metrics"]],
        )

    def test_nested_graph_does_not_leak_input_fields(self):
        """ainvoke는 ctx·scoped까지 돌려준다. 그것을 바깥 update로 올리면
        나중에 리듀서가 붙는 순간 조용히 중복이 생긴다."""
        from src.application.subgraphs.base import _PROCESS_OUTPUT

        self.assertNotIn("ctx", _PROCESS_OUTPUT)
        self.assertNotIn("scoped", _PROCESS_OUTPUT)

    def test_probe_fields_default_empty(self):
        state = SubgraphState(ctx=CTX)
        self.assertEqual(state.probe_records, [])
        self.assertEqual(state.probe_rounds, 0)
        self.assertEqual(state.probed, [])

    def test_probe_rounds_default_is_zero(self):
        """기본이 0이라 config에서 올리지 않으면 probe가 아예 안 돈다."""
        self.assertEqual(SubgraphConfig().max_probe_rounds, 0)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_process_graph.py -v`
Expected: FAIL — `AttributeError: 'MethodSubgraph' object has no attribute 'build_process'`

- [ ] **Step 3: 상태와 config에 필드를 더한다**

`src/application/subgraphs/base.py`의 `SubgraphConfig`에 추가:

```python
    #: 추가 조회를 몇 라운드까지 허용할지. 0이면 probe가 아예 돌지 않는다.
    #: 기본이 0이라 이 기능이 켜졌는지가 config에 드러난다.
    max_probe_rounds: int = 0
```

같은 파일 `SubgraphState`에 추가 (`records` 아래):

```python
    #: probe가 가져온 것. 리듀서 대신 노드가 명시적으로 누적한다 —
    #: 중첩 그래프 경계에서 리듀서는 같은 값을 두 번 쌓는다.
    probe_records: list[Record] = Field(default_factory=list)
    #: 돈 라운드 수. 상한과 비교한다.
    probe_rounds: int = 0
    #: 이미 돌린 probe 이름. 후보 목록에서 뺀다.
    probed: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: 슬롯을 일반화한다**

같은 파일, `SLOT_ERROR_NODE` 아래에 상수와 헬퍼를 둔다:

```python
#: 중첩 그래프가 바깥으로 올려보내는 필드. ctx·scoped는 입력이라 제외한다.
#: ainvoke가 전체 상태를 돌려주므로 골라내지 않으면 입력까지 다시 쓰게 된다.
_PROCESS_OUTPUT = (
    "records",
    "probe_records",
    "metrics",
    "judgements",
    "probe_rounds",
    "probed",
    "guardrail_drops",
)


def _run_nested(compiled) -> Callable:
    """컴파일된 그래프를 슬롯 노드로 감싼다."""

    async def node(state: SubgraphState) -> dict:
        result = await compiled.ainvoke(state)
        return {k: result[k] for k in _PROCESS_OUTPUT if k in result}

    return node
```

`BaseSubgraph`의 `process` 메서드 바로 위에 추가:

```python
    def build_process(self):
        """중첩 그래프를 쓰려면 컴파일된 그래프를 돌려준다.

        None이면 process 메서드를 쓴다. 기존 서브그래프가 전부 이 경로이므로
        이 확장으로 동작이 바뀌지 않는다.
        """
        return None
```

`compile()`의 process 노드 등록을 교체:

```python
        # 조립 시점에 한 번만 부른다. 그래프 모양이 실행마다 바뀌면
        # 체크포인트가 불안정해진다.
        nested = self.build_process()
        process = self.process if nested is None else _run_nested(nested)
        g.add_node(
            "process",
            guarded(key, SLOT_PROCESS, "generate_output")(process),
            destinations=("generate_output", SLOT_ERROR_NODE),
        )
```

- [ ] **Step 5: generate_output이 State의 drops를 함께 올리게 한다**

같은 파일 `generate_output`의 반환을 교체:

```python
        return {
            "section": section,
            "traces": self.deps.llm.drain_traces(),
            # probe 실패 기록은 State에 쌓이고 가드레일 폐기는 어댑터에 쌓인다.
            # 둘 다 올려야 어느 쪽도 조용히 사라지지 않는다.
            "guardrail_drops": (
                state.guardrail_drops + self.deps.llm.drain_guardrail_drops()
            ),
        }
```

`build_process()`를 안 쓰는 서브그래프는 `state.guardrail_drops`가 빈 목록이라 동작이 바뀌지 않는다.

**`src/application/nodes/outputs.py`의 `no_llm`도 같이 고친다.** 이것은 config `nodes.output`으로 `generate_output`을 대체하는 슬롯 부품이고, 역시 어댑터를 drain한다. 여기를 빠뜨리면 그 슬롯을 쓰는 서브그래프에서 probe 실패 기록이 조용히 사라진다 — `tests/test_graph_behaviour.py`에 `kpi.check`를 `outputs.no_llm`으로 교체하는 테스트가 실재한다.

```python
    return {
        "section": section,
        "traces": self.deps.llm.drain_traces(),
        # generate_output과 같은 이유로 둘을 합친다. 한쪽만 올리면
        # 이 슬롯을 쓰는 서브그래프에서만 기록이 사라져 찾기 어렵다.
        "guardrail_drops": (
            state.guardrail_drops + self.deps.llm.drain_guardrail_drops()
        ),
    }
```

- [ ] **Step 6: 통과와 회귀를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_process_graph.py -v`
Expected: PASS (5건)

Run: `.venv/bin/python -m pytest && .venv/bin/python -m unittest discover -s tests -t .`
Expected: 둘 다 통과. `tests/test_graph_behaviour.py`가 손대지 않고 통과하는 것이 핵심 신호다.

- [ ] **Step 7: 커밋**

```bash
git add src/application/subgraphs/base.py src/application/nodes/outputs.py tests/test_process_graph.py
git commit -m "Let a subgraph supply a compiled graph for its process slot

build_process returns None by default, so every existing subgraph keeps
using its process method and nothing about their behaviour changes.

The nested graph's ainvoke hands back the whole state, inputs included,
so the wrapper lifts only the fields process actually produces — passing
all of it up would double-apply any reducer added to those fields later."
```

---

### Task 2: ProbeDecision과 LLMPort.decide

`decide_next`가 쓸 구조화 출력 경로를 만든다. `plan()`·`judge()`와 같은 단일 경로를 지나므로 기록과 replay가 그대로 적용된다.

**Files:**
- Modify: `src/domain/models.py`
- Modify: `src/domain/ports.py`
- Modify: `src/infrastructure/llm.py`
- Test: `tests/test_process_graph.py` (이어서)

**Interfaces:**
- Produces: `ProbeDecision(next_step: str, reason: str)`, `BaseLLMAdapter.decide(node: str, prompt: str, allowed: list[str]) -> ProbeDecision`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_process_graph.py`에 추가. 상단 import에 보강:

```python
from src.domain.models import ProbeDecision
from src.infrastructure.llm import FakeLLMAdapter
```

본문에 추가:

```python
DONE = "done"


class DecideTest(unittest.TestCase):
    """분기 선택도 코드가 대조한다. 잘못된 선택은 계속 파는 쪽이 아니라
    멈추는 쪽으로 넘어져야 한다."""

    def decide(self, allowed):
        llm = FakeLLMAdapter(model="fake-local", seed="test")
        decision = asyncio.run(llm.decide("test.node", "라운드 0 · 이미 돈 것 없음", allowed))
        return decision, llm

    def test_picks_from_the_allowed_list(self):
        decision, _ = self.decide(["alarms", "equipment", DONE])
        self.assertIn(decision.next_step, ["alarms", "equipment", DONE])

    def test_unknown_choice_becomes_done(self):
        class Rogue(FakeLLMAdapter):
            async def _decide_raw(self, prompt, allowed):
                return ProbeDecision(next_step="nonexistent.probe", reason="…")

        llm = Rogue(model="fake-local")
        decision = asyncio.run(llm.decide("test.node", "프롬프트", ["alarms", DONE]))
        self.assertEqual(decision.next_step, DONE)
        self.assertTrue(any("nonexistent.probe" in d for d in llm.guardrail_drops))

    def test_records_a_trace(self):
        """replay가 되려면 프롬프트와 응답이 남아야 한다."""
        _, llm = self.decide(["alarms", DONE])
        traces = llm.drain_traces()
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].node, "test.node")

    def test_empty_allowed_list_is_done(self):
        decision, _ = self.decide([])
        self.assertEqual(decision.next_step, DONE)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_process_graph.py -k Decide -v`
Expected: FAIL — `ImportError: cannot import name 'ProbeDecision'`

- [ ] **Step 3: 모델과 포트를 더한다**

`src/domain/models.py`의 `Requirement` 아래에 넣는다:

```python
class ProbeDecision(BaseModel):
    """다음에 무엇을 볼지. 목적지 이름만 고른다 — 조회는 코드가 한다.

    next_step이 허용목록 밖이면 어댑터가 "done"으로 바꾼다. 잘못된 선택은
    계속 파는 쪽이 아니라 멈추는 쪽으로 넘어져야 한다.
    """

    next_step: str = "done"
    reason: str = ""
```

`src/domain/ports.py`의 `LLMPort`에 추가 (import에 `ProbeDecision` 보강):

```python
    async def decide(
        self, node: str, prompt: str, allowed: list[str]
    ) -> ProbeDecision: ...
```

- [ ] **Step 4: 어댑터에 decide 경로를 만든다**

`src/infrastructure/llm.py` 상단 import에 `ProbeDecision`을 더한다.

`BaseLLMAdapter`의 `_plan_raw` 아래에 추상 메서드를 더한다:

```python
    async def _decide_raw(self, prompt: str, allowed: list[str]) -> ProbeDecision:
        """프롬프트 → 다음 목적지. 검증 전 raw."""
        raise NotImplementedError
```

`plan()` 아래에 넣는다:

```python
    #: 더 볼 것이 없다는 선택. 허용목록 밖 값도 이것으로 넘어뜨린다.
    DONE = "done"

    async def decide(
        self, node: str, prompt: str, allowed: list[str]
    ) -> ProbeDecision:
        """다음 목적지를 받아 허용목록과 대조한다.

        judge()가 evidence를, plan()이 selected를 대조하는 것과 같은 구조다.
        검사기가 LLM이 아니라 집합 연산이므로 검사 자체가 틀릴 수 없다.
        """
        if not allowed:
            return ProbeDecision(next_step=self.DONE, reason="후보가 없습니다")

        key = self._replay_key(node, prompt)
        if key in self._replay:
            payload = self._replay[key]
            raw = ProbeDecision(**json.loads(payload))
            self._record(node, prompt, payload, replayed=True)
        else:
            raw = await self._decide_raw(prompt, allowed)
            payload = json.dumps(raw.model_dump(mode="json"), ensure_ascii=False)
            self._record(node, prompt, payload)

        if raw.next_step not in allowed:
            self.guardrail_drops.append(
                f"{node}: 허용되지 않은 목적지 {raw.next_step!r}을 골라 중단합니다"
            )
            return ProbeDecision(next_step=self.DONE, reason=raw.reason)
        return raw
```

`FakeLLMAdapter`에 추가:

```python
    async def _decide_raw(self, prompt: str, allowed: list[str]) -> ProbeDecision:
        """남은 후보 중 첫 번째를 고른다.

        _judge_raw·_plan_raw는 가드레일 시연용으로 일부러 잘못된 값을 섞지만
        **여기서는 그러지 않는다.** 잘못된 근거는 판정 하나를 버리는 데
        그치지만 잘못된 목적지는 루프를 끝내므로, 같은 관례를 쓰면 실제
        실행에서 probe가 매번 꺼진 것처럼 보인다. 이 가드레일은
        test_unknown_choice_becomes_done이 명시적 하위 클래스로 검증한다.
        """
        pick = next((a for a in allowed if a != self.DONE), self.DONE)
        return ProbeDecision(next_step=pick, reason="첫 후보를 선택했습니다")
```

`ChatModelAdapter`에 추가:

```python
    async def _decide_raw(self, prompt: str, allowed: list[str]) -> ProbeDecision:
        """★ 실제 호출 지점 (분기 선택).

        도구 호출도 루프도 없는 단발 호출이다. 목적지 하나만 고르면 된다.
        """
        listing = "\n".join(f"  {a}" for a in allowed)
        structured = self._ensure_client().with_structured_output(ProbeDecision)
        return await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "다음에 무엇을 확인할지 하나만 고르세요. next_step에는 "
                        "아래 목록에 있는 값만 넣으세요. 더 볼 것이 없으면 "
                        f"'done'을 고르세요.\n{listing}"
                    ),
                },
                {"role": "user", "content": prompt},
            ]
        )
```

- [ ] **Step 5: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_process_graph.py -v`
Expected: PASS (9건)

Run: `.venv/bin/python -m pytest && .venv/bin/python -m unittest discover -s tests -t .`
Expected: 둘 다 통과

- [ ] **Step 6: 커밋**

```bash
git add src/domain/models.py src/domain/ports.py src/infrastructure/llm.py tests/test_process_graph.py
git commit -m "Add a guarded decide() path for choosing the next probe

Mirrors plan() and judge(): the model returns a structured choice and
code — not another model — checks it against the allowed list. An
unrecognised destination becomes 'done', so a bad choice stops the loop
rather than sending it somewhere undeclared."
```

---

### Task 3: Probe와 ProcessGraph 조립

중첩 그래프를 엮는 곳이다. probe의 `kinds`가 `required_kinds`를 벗어나면 여기서 부팅이 멈춘다.

**Files:**
- Create: `src/application/subgraphs/process_graph.py`
- Test: `tests/test_process_graph.py` (이어서)

**Interfaces:**
- Consumes: `SubgraphState`, `_run_nested` (Task 1), `BaseLLMAdapter.decide` (Task 2)
- Produces: `Probe` (속성 `name`/`kinds`/`description`, 메서드 `compile(deps, config)`), `ProcessGraph(subgraph, probes)` with `.compile()`, `probe_guarded(name)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_process_graph.py`에 추가. 상단 import에 보강:

```python
from src.application.subgraphs.process_graph import Probe, ProcessGraph
```

본문에 추가:

```python
class StubProbe(Probe):
    """단일 노드 probe. 여러 노드여도 되지만 여기서는 최소로 둔다."""

    def __init__(self, name, kinds, marker=None, boom=False):
        self.name = name
        self.kinds = kinds
        self.description = f"{name} 확인"
        self._marker = marker or f"{name}-rec"
        self._boom = boom

    def compile(self, deps, config):
        async def step(state: SubgraphState) -> dict:
            if self._boom:
                raise RuntimeError("probe 조회 실패")
            return {"probe_records": [*state.probe_records, Record(id=self._marker)]}

        g = StateGraph(SubgraphState)
        g.add_node("step", step)
        g.add_edge(START, "step")
        g.add_edge("step", END)
        return g.compile()


class ProbingSubgraph(BaseSubgraph):
    registry_name = "test.probing"
    title = "probe 있는 분석"
    required_kinds = ("kpi", "alarms", "equipment_status")
    probes = ()

    async def fetch(self, state: SubgraphState) -> dict:
        return {"records": [Record(id="base-1")]}

    async def compute(self, state: SubgraphState) -> dict:
        return {"metrics": [Metric(name="지표", value=len(state.records))]}

    async def judge(self, state: SubgraphState) -> dict:
        seen = state.records + state.probe_records
        return {"judgements": [Judgement(
            subject=f"판정 {len(seen)}건", severity=Severity.WARNING,
            reasoning="…", evidence=[r.id for r in seen])]}

    def build_process(self):
        return ProcessGraph(self, self.probes).compile()


def run_probing(probes, max_rounds, llm=None):
    cls = type("Sub", (ProbingSubgraph,), {"probes": probes})
    sub = cls(SubgraphConfig(enabled=True, max_probe_rounds=max_rounds),
              Dependencies(llm=llm or FakeLLMAdapter(model="fake-local", seed="t")))
    compiled = sub.compile()
    return asyncio.run(compiled.ainvoke(SubgraphState(ctx=CTX, scoped=CTX)))


class ProcessGraphTest(unittest.TestCase):
    def test_probe_kinds_must_be_declared(self):
        """선언하지 않은 데이터는 열 수 없다. 조립 시점에 막는다."""
        sub = ProbingSubgraph(SubgraphConfig(), Dependencies())
        with self.assertRaises(ValueError) as caught:
            ProcessGraph(sub, (StubProbe("rogue", ("material_stock",)),))
        self.assertIn("material_stock", str(caught.exception))

    def test_declared_kinds_are_accepted(self):
        sub = ProbingSubgraph(SubgraphConfig(), Dependencies())
        ProcessGraph(sub, (StubProbe("alarms", ("alarms",)),))  # 예외 없음

    def test_judgements_are_replaced_not_appended(self):
        """라운드마다 새로 만들어 덮어쓴다. 한 섹션에 모순된 판정이 남으면 안 된다."""
        out = run_probing((StubProbe("alarms", ("alarms",)),), max_rounds=1)
        self.assertEqual(len(out["judgements"]), 1)
        self.assertIn("2건", out["judgements"][0].subject)

    def test_probe_records_accumulate_without_duplicates(self):
        out = run_probing(
            (StubProbe("alarms", ("alarms",)),
             StubProbe("equipment", ("equipment_status",))),
            max_rounds=2,
        )
        self.assertEqual([r.id for r in out["probe_records"]],
                         ["alarms-rec", "equipment-rec"])

    def test_probe_evidence_survives_the_guardrail(self):
        """probe로 늘어난 record를 인용한 판정이 폐기되면 안 된다."""
        out = run_probing((StubProbe("alarms", ("alarms",)),), max_rounds=1)
        evidence = out["judgements"][0].evidence
        self.assertIn("alarms-rec", evidence)
```

파일 상단 import에 `Judgement`, `Severity`를 보강한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_process_graph.py -k ProcessGraph -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.application.subgraphs.process_graph'`

- [ ] **Step 3: 구현한다**

`src/application/subgraphs/process_graph.py` 신규:

```python
"""process를 중첩 그래프로 엮는 곳.

    fetch → compute → judge → decide_next ─┬─ probe_a ─┐
                                 ↑          ├─ probe_b ─┤
                                 └──────────┴───────────┘
                                            └─ done → END

LLM은 **목적지 이름만** 고른다. 멈출 시점과 각 probe가 무엇을 조회할지는
코드가 쥔다. 오류 탐지는 모델이 약한 쪽이라, 그만둘 판단을 맡기지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.application.subgraphs.base import SubgraphState, _run_nested

logger = logging.getLogger(__name__)

DECIDE_NODE = "decide_next"
DONE = "done"


class Probe:
    """추가 조회 한 갈래. 자체가 여러 노드로 된 그래프일 수 있다.

    kinds가 이 probe가 열 수 있는 데이터 종류다. 조립 시점에 서브그래프의
    required_kinds 안에 있는지 검사하므로, 선언하지 않은 곳은 열 수 없다.
    """

    name: str = ""
    kinds: tuple[str, ...] = ()
    #: decide_next 프롬프트에 실린다. 이름만으로는 무엇을 보는지 알 수 없다.
    description: str = ""

    def compile(self, deps: Any, config: Any):
        """SubgraphState 위에서 도는 컴파일된 그래프를 돌려준다.

        노드가 state를 받으므로 scoped도 state.scoped로 들어온다. as_of가
        인자로 조작되지 않는다는 뜻이고, 이것이 시점 재현성의 근거다.
        """
        raise NotImplementedError


def probe_guarded(name: str) -> Callable:
    """probe 실패를 삼키고 decide_next로 돌려보낸다.

    선택적 보강이 실패했다고 본체 판정을 버리는 것은 과하다. 다른 probe를
    고르거나 끝내면 되고, base 판정과 지표는 이미 State에 있다.
    """

    def decorator(fn: Callable) -> Callable:
        async def wrapper(state: SubgraphState) -> Command:
            try:
                update = await fn(state)
            except Exception as exc:  # noqa: BLE001 - probe 경계에서 격리한다
                logger.exception("probe=%s 실패", name)
                return Command(
                    goto=DECIDE_NODE,
                    update={"guardrail_drops": [
                        *state.guardrail_drops,
                        f"probe:{name} 실패로 건너뜁니다 ({type(exc).__name__})",
                    ]},
                )
            return Command(goto="judge", update=update)

        wrapper.__name__ = f"probe_{name}"
        return wrapper

    return decorator


class ProcessGraph:
    """서브그래프의 fetch/compute/judge와 probe들을 하나로 엮는다."""

    def __init__(self, subgraph: Any, probes: tuple[Probe, ...]) -> None:
        declared = set(subgraph.required_kinds)
        unknown = {k for p in probes for k in p.kinds} - declared
        if unknown:
            raise ValueError(
                f"{subgraph.registry_name}의 probe가 required_kinds에 없는 "
                f"{sorted(unknown)}을(를) 요청합니다. required_kinds에 선언하세요."
            )
        self.subgraph = subgraph
        self.probes = probes

    # ------------------------------------------------------------------
    def _prompt(self, state: SubgraphState, remaining: list[str]) -> str:
        """라운드와 이미 판 곳을 싣는다.

        replay 키가 프롬프트 해시라, 라운드마다 프롬프트가 같으면 저장된
        응답이 반복 재생되어 루프가 끝나지 않는다.
        """
        catalog = "\n".join(
            f"- {p.name}: {p.description}" for p in self.probes if p.name in remaining
        )
        findings = "\n".join(f"- {j.subject}: {j.reasoning}" for j in state.judgements)
        return (
            f"[라운드 {state.probe_rounds}] 이미 확인한 것: "
            f"{', '.join(state.probed) or '없음'}\n\n"
            f"현재 판정:\n{findings}\n\n"
            f"추가로 확인할 수 있는 것:\n{catalog}\n\n"
            "판정을 확정하는 데 더 볼 것이 있으면 하나 고르고, 충분하면 "
            f"'{DONE}'을 고르세요."
        )

    async def decide_next(self, state: SubgraphState) -> Command:
        remaining = [p.name for p in self.probes if p.name not in state.probed]
        cap = self.subgraph.config.max_probe_rounds
        # 상한과 후보 소진은 LLM에게 묻지 않는다. 멈출 시점은 코드가 쥔다.
        if state.probe_rounds >= cap or not remaining:
            return Command(goto=END)

        decision = await self.subgraph.deps.llm.decide(
            self.subgraph.registry_name,
            self._prompt(state, remaining),
            [*remaining, DONE],
        )
        if decision.next_step == DONE:
            return Command(goto=END)

        return Command(
            goto=f"probe_{decision.next_step}",
            update={
                "probe_rounds": state.probe_rounds + 1,
                # 실패한 probe도 여기 남으므로 무한히 재시도하지 않는다.
                "probed": [*state.probed, decision.next_step],
            },
        )

    # ------------------------------------------------------------------
    def compile(self):
        sub = self.subgraph
        g = StateGraph(SubgraphState)
        g.add_node("fetch", sub.fetch)
        g.add_node("compute", sub.compute)
        g.add_node("judge", sub.judge)

        names = [f"probe_{p.name}" for p in self.probes]
        g.add_node(DECIDE_NODE, self.decide_next, destinations=(*names, END))
        for probe in self.probes:
            inner = _run_nested(probe.compile(sub.deps, sub.config))
            g.add_node(f"probe_{probe.name}",
                       probe_guarded(probe.name)(inner),
                       destinations=("judge", DECIDE_NODE))

        g.add_edge(START, "fetch")
        g.add_edge("fetch", "compute")
        g.add_edge("compute", "judge")
        g.add_edge("judge", DECIDE_NODE)
        return g.compile()
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_process_graph.py -v`
Expected: PASS (14건)

Run: `.venv/bin/python -m pytest && .venv/bin/python -m unittest discover -s tests -t .`
Expected: 둘 다 통과

- [ ] **Step 5: 커밋**

```bash
git add src/application/subgraphs/process_graph.py tests/test_process_graph.py
git commit -m "Wire fetch, compute, judge and probes into one nested graph

A probe declares the kinds it opens and those must already appear in the
subgraph's required_kinds, so assembly fails at boot rather than the
analysis quietly reading something the deployment never mapped.

judge re-runs on every round over the accumulated records and replaces
its judgements wholesale — contradictory findings inside one section
would be worse than the extra call the cap already bounds."
```

---

### Task 4: 상한과 후보 관리

멈출 시점을 코드가 쥐고 있는지 확인한다. 이 태스크는 Task 3에서 만든 `decide_next`의 동작을 테스트로 못 박는 것이 본체다.

**Files:**
- Test: `tests/test_process_graph.py` (이어서)
- Modify: `src/application/subgraphs/process_graph.py` (필요 시에만)

**Interfaces:**
- Consumes: `ProcessGraph.decide_next` (Task 3)
- Produces: 없음 (동작 보증)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_process_graph.py`에 추가:

```python
class CountingLLM(FakeLLMAdapter):
    """decide 호출 횟수를 센다. 상한 판단이 코드에 있는지 보려는 것이다."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.decide_calls = 0

    async def _decide_raw(self, prompt, allowed):
        self.decide_calls += 1
        return await super()._decide_raw(prompt, allowed)


class ProbeCapTest(unittest.TestCase):
    def test_zero_rounds_never_calls_the_model(self):
        """기본값 0이면 probe가 안 돌고 LLM도 안 부른다."""
        llm = CountingLLM(model="fake-local", seed="t")
        out = run_probing((StubProbe("alarms", ("alarms",)),), max_rounds=0, llm=llm)
        self.assertEqual(llm.decide_calls, 0)
        self.assertEqual(out["probe_records"], [])
        self.assertEqual(out["probed"], [])

    def test_stops_at_the_cap_without_asking(self):
        """상한에 닿으면 LLM을 부르지 않고 끝낸다."""
        llm = CountingLLM(model="fake-local", seed="t")
        out = run_probing(
            (StubProbe("alarms", ("alarms",)),
             StubProbe("equipment", ("equipment_status",))),
            max_rounds=1,
            llm=llm,
        )
        self.assertEqual(len(out["probed"]), 1)
        # 라운드 0에서 한 번 묻고, 상한에 닿은 뒤로는 묻지 않는다.
        self.assertEqual(llm.decide_calls, 1)

    def test_exhausted_candidates_stop_without_asking(self):
        """후보를 다 돌면 상한이 남아도 끝낸다."""
        llm = CountingLLM(model="fake-local", seed="t")
        out = run_probing((StubProbe("alarms", ("alarms",)),), max_rounds=5, llm=llm)
        self.assertEqual(out["probed"], ["alarms"])
        self.assertEqual(llm.decide_calls, 1)

    def test_a_probe_is_never_offered_twice(self):
        out = run_probing(
            (StubProbe("alarms", ("alarms",)),
             StubProbe("equipment", ("equipment_status",))),
            max_rounds=5,
        )
        self.assertEqual(sorted(out["probed"]), ["alarms", "equipment"])
        self.assertEqual(len(out["probed"]), len(set(out["probed"])))

    def test_prompt_differs_between_rounds(self):
        """프롬프트가 같으면 replay가 같은 응답을 재생해 루프가 끝나지 않는다."""
        out = run_probing(
            (StubProbe("alarms", ("alarms",)),
             StubProbe("equipment", ("equipment_status",))),
            max_rounds=2,
        )
        # generate_output이 이미 어댑터를 비우므로 반환된 State에서 꺼낸다.
        # 여기서 llm.drain_traces()를 다시 부르면 빈 목록이 와서 이 테스트가
        # 무엇을 하든 통과해버린다.
        prompts = [t.prompt for t in out["traces"] if "[라운드" in t.prompt]
        self.assertGreaterEqual(len(prompts), 2, "decide가 두 번 이상 불려야 한다")
        self.assertEqual(len(prompts), len(set(prompts)))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_process_graph.py -k ProbeCap -v`
Expected: 대부분 PASS, 실패하는 것이 있으면 Task 3 구현의 결함이다. 특히 `test_zero_rounds_never_calls_the_model`이 실패하면 `decide_next`가 상한 검사보다 LLM 호출을 먼저 하고 있다는 뜻이다.

> 이 태스크는 새 기능이 아니라 Task 3 동작의 계약을 고정하는 것이다. 테스트가 처음부터 통과하면 그대로 두고, 실패하면 Step 3에서 `decide_next`를 고친다.

- [ ] **Step 3: 실패한 테스트가 있으면 고친다**

`decide_next`의 순서를 확인한다. 상한과 후보 검사가 **`deps.llm.decide` 호출보다 먼저** 와야 한다:

```python
        remaining = [p.name for p in self.probes if p.name not in state.probed]
        cap = self.subgraph.config.max_probe_rounds
        if state.probe_rounds >= cap or not remaining:
            return Command(goto=END)          # 여기서 끝. LLM을 부르지 않는다.

        decision = await self.subgraph.deps.llm.decide(...)
```

`test_prompt_differs_between_rounds`가 실패하면 `_prompt`가 라운드 번호나 `probed`를 싣지 않고 있다.

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_process_graph.py -v`
Expected: PASS (19건)

Run: `.venv/bin/python -m pytest && .venv/bin/python -m unittest discover -s tests -t .`
Expected: 둘 다 통과

- [ ] **Step 5: 커밋**

```bash
git add tests/test_process_graph.py src/application/subgraphs/process_graph.py
git commit -m "Pin down that the cap is enforced without asking the model

With max_probe_rounds at its default of zero the model is never called
at all, so turning the feature off costs nothing. Reaching the cap or
running out of candidates also ends the loop in code.

Also pins that each round's prompt differs: replay keys on the prompt
hash, so identical prompts would replay one stored answer forever."
```

---

### Task 5: probe 실패 격리

선택적 보강이 실패했다고 분석 본체를 버리지 않는다.

**Files:**
- Test: `tests/test_process_graph.py` (이어서)
- Modify: `src/application/subgraphs/process_graph.py` (필요 시에만)

**Interfaces:**
- Consumes: `probe_guarded` (Task 3)
- Produces: 없음 (동작 보증)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_process_graph.py`에 추가:

```python
class ProbeFailureTest(unittest.TestCase):
    """probe는 보강이다. 실패해도 본체 판정과 지표는 살아남아야 한다."""

    def test_failure_keeps_the_base_analysis(self):
        out = run_probing(
            (StubProbe("alarms", ("alarms",), boom=True),), max_rounds=1
        )
        self.assertTrue(out["judgements"], "본체 판정이 남아야 한다")
        self.assertTrue(out["metrics"], "지표가 남아야 한다")
        self.assertEqual([r.id for r in out["records"]], ["base-1"])

    def test_failure_is_recorded(self):
        out = run_probing(
            (StubProbe("alarms", ("alarms",), boom=True),), max_rounds=1
        )
        self.assertTrue(any("probe:alarms" in d for d in out["guardrail_drops"]))

    def test_failure_does_not_produce_a_degraded_section(self):
        """섹션 자체는 정상이어야 한다. degraded는 본체가 죽었을 때만이다."""
        out = run_probing(
            (StubProbe("alarms", ("alarms",), boom=True),), max_rounds=1
        )
        # pydantic State의 필드는 생성 시 넘기지도, 어느 노드도 쓰지도 않으면
        # ainvoke 결과 dict에 아예 없다 — None으로 들어있는 것이 아니다.
        # out["error"]로 쓰면 정상 경로에서 KeyError가 난다.
        self.assertIsNone(out.get("error"))
        self.assertFalse(out["section"].degraded)

    def test_failed_probe_is_not_retried(self):
        """실패한 probe도 probed에 남아 후보에서 빠진다."""
        out = run_probing(
            (StubProbe("alarms", ("alarms",), boom=True),
             StubProbe("equipment", ("equipment_status",))),
            max_rounds=5,
        )
        self.assertEqual(out["probed"].count("alarms"), 1)

    def test_other_probes_still_run_after_a_failure(self):
        out = run_probing(
            (StubProbe("alarms", ("alarms",), boom=True),
             StubProbe("equipment", ("equipment_status",))),
            max_rounds=5,
        )
        self.assertIn("equipment-rec", [r.id for r in out["probe_records"]])

    def test_body_failure_still_degrades_the_section(self):
        """fetch·judge는 본체다. 그쪽 실패는 지금처럼 degraded가 되어야 한다."""

        class Broken(ProbingSubgraph):
            probes = ()

            async def fetch(self, state):
                raise RuntimeError("본체 조회 실패")

        sub = Broken(SubgraphConfig(enabled=True), Dependencies(
            llm=FakeLLMAdapter(model="fake-local", seed="t")))
        out = asyncio.run(sub.compile().ainvoke(SubgraphState(ctx=CTX, scoped=CTX)))
        self.assertTrue(out["section"].degraded)

    def test_degradation_path_survives_a_broken_adapter(self):
        """handle_error는 guarded() 없이 등록된다. 거기서 예외가 나면 서브그래프를
        탈출해 리포트 전체를 죽인다 — degraded 경로가 다른 무엇에도 기대면 안 된다."""

        class BrokenAdapter(FakeLLMAdapter):
            def drain_traces(self):
                raise RuntimeError("어댑터 고장")

        class Broken(ProbingSubgraph):
            probes = ()

            async def fetch(self, state):
                raise RuntimeError("본체 조회 실패")

        sub = Broken(SubgraphConfig(enabled=True),
                     Dependencies(llm=BrokenAdapter(model="fake-local")))
        out = asyncio.run(sub.compile().ainvoke(SubgraphState(ctx=CTX, scoped=CTX)))
        self.assertTrue(out["section"].degraded, "섹션은 degraded로 살아남아야 한다")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_process_graph.py -k ProbeFailure -v`
Expected: `test_failure_keeps_the_base_analysis`가 통과하면 `probe_guarded`가 이미 동작하는 것이다. 실패하면 probe 예외가 중첩 그래프를 뚫고 나가 `handle_error`로 가고 있다는 뜻이다.

- [ ] **Step 3: 실패한 테스트가 있으면 고친다**

`probe_guarded`가 probe 노드를 **감싸고 있는지** 확인한다. Task 3의 `compile()`에서:

```python
            g.add_node(f"probe_{probe.name}",
                       probe_guarded(probe.name)(inner),
                       destinations=("judge", DECIDE_NODE))
```

`destinations`에 `DECIDE_NODE`가 빠져 있으면 실패 경로가 열리지 않는다.

`test_failure_is_recorded`가 실패하면 `guardrail_drops`가 바깥으로 올라오지 않는 것이다. Task 1의 `_PROCESS_OUTPUT`에 `"guardrail_drops"`가 있는지, `generate_output`이 `state.guardrail_drops`를 합치는지 확인한다.

**`test_degradation_path_survives_a_broken_adapter`는 반드시 실패한다.** `handle_error`가 `guarded()` 없이 등록되는데(`base.py`의 `g.add_node(SLOT_ERROR_NODE, self.handle_error)`) 어댑터를 drain하므로, 거기서 예외가 나면 서브그래프를 탈출해 리포트 전체를 죽인다. 이것은 선행 프로젝트에서 park된 항목이며 실패 격리를 다루는 이 태스크가 그 자리다.

`src/application/subgraphs/base.py`의 `handle_error`에서 drain을 방어한다:

```python
    async def handle_error(self, state: SubgraphState) -> dict:
        err = state.error
        section = ReportSection(...)  # 기존 그대로
        # 이 노드는 guarded() 없이 등록된다. 여기서 예외가 나면 서브그래프를
        # 탈출해 리포트 전체가 죽는다 — 한 섹션을 살리려는 경로가 전체를
        # 죽이는 것은 본말전도다. 기록을 잃더라도 degraded 섹션은 내보낸다.
        try:
            traces = self.deps.llm.drain_traces()
            drops = self.deps.llm.drain_guardrail_drops()
        except Exception:  # noqa: BLE001 - 마지막 방어선이다
            logger.exception("degraded 경로에서 관측 기록을 회수하지 못했습니다")
            traces, drops = [], []
        return {
            "section": section,
            "traces": traces,
            "guardrail_drops": state.guardrail_drops + drops,
        }
```

`logging`과 모듈 `logger`가 `base.py`에 없으면 함께 추가한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_process_graph.py -v`
Expected: PASS (25건)

Run: `.venv/bin/python -m pytest && .venv/bin/python -m unittest discover -s tests -t .`
Expected: 둘 다 통과

- [ ] **Step 5: 커밋**

```bash
git add tests/test_process_graph.py src/application/subgraphs/process_graph.py
git commit -m "Keep a failed probe from discarding the analysis body

A probe is an optional enrichment. When one fails the loop returns to
decide_next, which can pick another or stop, and the base metrics and
judgements survive with the section still marked healthy.

fetch, compute and judge are the body — their failures still degrade the
section exactly as before."
```

---

### Task 6: kpi.check 적용

첫 실사용이다. 이 태스크가 끝나면 기능이 실제 리포트에서 동작한다.

**Files:**
- Modify: `src/application/subgraphs/kpi/check.py`
- Modify: `config/gbm/mx.json`
- Test: `tests/test_kpi_probes.py` (신규)

**Interfaces:**
- Consumes: `Probe`, `ProcessGraph` (Task 3), `build_process` (Task 1)
- Produces: `EquipmentProbe`, `AlarmProbe`, `KpiCheck.fetch/compute/judge`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_kpi_probes.py` 신규:

```python
"""kpi.check의 추가 조회.

KPI 미달이 보이면 설비 상태나 알람을 더 보고 판정을 다시 만든다.
"""

from __future__ import annotations

import asyncio
import unittest

from src.application.graph.state import Dependencies
from src.application.subgraphs.base import SubgraphState
from src.application.subgraphs.kpi.check import KpiCheck, KpiCheckConfig
from src.domain.models import SnapshotContext
from src.infrastructure.llm import FakeLLMAdapter
from tests.helpers import AS_OF, base_config, temp_config, with_subgraph_patch

CTX = SnapshotContext(as_of=AS_OF, gbm="mx", factory="gumi")


class FakeRouter:
    """kind별로 정해진 record를 돌려준다. 어느 kind가 요청됐는지 기록한다."""

    def __init__(self, by_kind):
        self.by_kind = by_kind
        self.asked = []

    async def fetch(self, ctx, spec):
        self.asked.append(spec.kind)
        return list(self.by_kind.get(spec.kind, []))

    def routed_kinds(self):
        return set(self.by_kind)


def run_kpi(max_rounds, by_kind):
    router = FakeRouter(by_kind)
    deps = Dependencies(data=router, llm=FakeLLMAdapter(model="fake-local", seed="t"))
    sub = KpiCheck(
        KpiCheckConfig(enabled=True, use_llm_judge=False, max_probe_rounds=max_rounds),
        deps,
    )
    out = asyncio.run(sub.compile().ainvoke(SubgraphState(ctx=CTX, scoped=CTX)))
    return out, router


def kpi_records():
    from src.domain.models import Record

    return [Record(id="kpi:수율", metadata={"kpi": "수율", "unit": "%"},
                   record={"value": 88.0, "target": 96.0, "gap_pct": -8.3})]


def equipment_records():
    from src.domain.models import Record

    return [Record(id="eq:L1:EQ-3", metadata={"line": "L1", "equipment_id": "EQ-3"},
                   record={"state": "DOWN"})]


class KpiProbeTest(unittest.TestCase):
    def test_declares_the_kinds_its_probes_open(self):
        """선언하지 않은 데이터는 열 수 없다."""
        for kind in ("kpi", "equipment_status", "production"):
            self.assertIn(kind, KpiCheck.required_kinds)

    def test_no_probe_when_rounds_are_zero(self):
        out, router = run_kpi(0, {"kpi": kpi_records()})
        self.assertEqual(router.asked, ["kpi"])
        self.assertEqual(out["probe_records"], [])

    def test_probe_fetches_a_declared_kind(self):
        out, router = run_kpi(
            1, {"kpi": kpi_records(), "equipment_status": equipment_records(),
                "production": []}
        )
        self.assertGreater(len(router.asked), 1)
        for kind in router.asked:
            self.assertIn(kind, KpiCheck.required_kinds)

    def test_metrics_come_from_base_records_only(self):
        """지표는 KPI에서만 나온다. probe가 지표를 늘리면 안 된다."""
        without, _ = run_kpi(0, {"kpi": kpi_records()})
        with_probe, _ = run_kpi(
            1, {"kpi": kpi_records(), "equipment_status": equipment_records(),
                "production": []}
        )
        self.assertEqual([m.name for m in without["metrics"]],
                         [m.name for m in with_probe["metrics"]])

    def test_threshold_judgement_survives_the_rounds(self):
        """judge가 판정을 통째로 교체해도 임계치 판정은 매번 다시 만들어진다."""
        out, _ = run_kpi(
            1, {"kpi": kpi_records(), "equipment_status": equipment_records(),
                "production": []}
        )
        subjects = [j.subject for j in out["judgements"]]
        self.assertTrue(any("수율" in s for s in subjects), subjects)

    def test_all_evidence_is_traceable(self):
        """판정의 근거가 전부 실제 record id여야 한다."""
        out, _ = run_kpi(
            1, {"kpi": kpi_records(), "equipment_status": equipment_records(),
                "production": []}
        )
        known = {r.id for r in out["records"]} | {r.id for r in out["probe_records"]}
        for judgement in out["judgements"]:
            for ev in judgement.evidence:
                self.assertIn(ev, known)


class KpiConfigTest(unittest.TestCase):
    def test_shipped_config_enables_probing(self):
        cfg = base_config()
        self.assertEqual(cfg["subgraphs"]["kpi.check"]["max_probe_rounds"], 2)

    def test_boot_validation_accepts_the_wider_kinds(self):
        """probe가 여는 kind가 ports에 매핑돼 있어야 부팅이 통과한다."""
        with temp_config(gbm=with_subgraph_patch(lambda c: None)) as deploy:
            ports = deploy.section("ports")
        for kind in KpiCheck.required_kinds:
            self.assertIn(kind, ports)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_kpi_probes.py -v`
Expected: FAIL — `test_declares_the_kinds_its_probes_open`에서 `equipment_status`가 `required_kinds`에 없다

- [ ] **Step 3: process를 세 단계로 나눈다**

`src/application/subgraphs/kpi/check.py`의 `process`를 `fetch`/`compute`/`judge`로 나눈다. 기존 `process` 메서드는 지운다 — `build_process()`가 그 자리를 대신한다.

```python
    async def fetch(self, state: SubgraphState) -> dict:
        return {"records": await self.deps.data.fetch(
            state.scoped, FetchSpec(kind="kpi"))}

    async def compute(self, state: SubgraphState) -> dict:
        """지표만 만든다. base records만 보므로 한 번만 돌면 된다."""
        metrics = []
        for rec in state.records:
            unit = rec.metadata.get("unit", "")
            metrics.append(
                Metric(
                    name=rec.metadata["kpi"],
                    value=rec.record["value"],
                    unit=unit,
                    note=(f"목표 {rec.record['target']}{unit} · "
                          f"괴리 {rec.record['gap_pct']:+.1f}%"),
                )
            )
        return {"metrics": metrics}

    async def judge(self, state: SubgraphState) -> dict:
        """판정을 통째로 새로 만든다.

        임계치 판정은 base records만 보므로 매 라운드 다시 계산해도 결과가
        같다. 그래서 교체해도 잃는 것이 없고, 한 섹션에 모순된 판정이
        남지도 않는다.
        """
        cfg: KpiCheckConfig = self.config
        judgements = []
        for rec in state.records:
            unit = rec.metadata.get("unit", "")
            gap = rec.record["gap_pct"]
            if gap <= cfg.critical_gap_pct:
                severity = Severity.CRITICAL
            elif gap <= cfg.warn_gap_pct:
                severity = Severity.WARNING
            else:
                continue
            judgements.append(
                Judgement(
                    subject=f"{rec.metadata['kpi']} 목표 미달",
                    severity=severity,
                    reasoning=(f"{rec.record['value']}{unit}로 목표 "
                               f"{rec.record['target']}{unit} 대비 {gap:+.1f}%입니다"),
                    evidence=[rec.id],
                )
            )

        # probe로 늘어난 것까지 근거 후보에 넣는다. 빠뜨리면 probe 데이터를
        # 인용한 판정이 통째로 폐기된다.
        seen = state.records + state.probe_records
        if cfg.use_llm_judge and seen:
            judgements.extend(await self.deps.llm.judge(
                self.registry_name, self._judge_prompt(seen), [r.id for r in seen]))
        return {"judgements": judgements}

    def _judge_prompt(self, records: list) -> str:
        facts = "\n".join(f"- [{r.id}] {r.metadata} {r.record}" for r in records)
        return (
            "아래 자료에서 개별 임계치로는 잡히지 않는 이상 신호가 있는지 "
            "판단하세요. 근거는 반드시 대괄호 안의 id로만 인용하세요.\n" + facts
        )
```

- [ ] **Step 4: probe 두 개와 build_process를 더한다**

같은 파일에 추가 (import에 `Probe`, `ProcessGraph`, `Record`, `StateGraph`, `START`, `END` 보강):

```python
class _KindProbe(Probe):
    """kind 하나를 그대로 가져오는 단일 노드 probe."""

    kind: str = ""

    def compile(self, deps, config):
        async def step(state: SubgraphState) -> dict:
            records = await deps.data.fetch(state.scoped, FetchSpec(kind=self.kind))
            return {"probe_records": [*state.probe_records, *records]}

        g = StateGraph(SubgraphState)
        g.add_node("step", step)
        g.add_edge(START, "step")
        g.add_edge("step", END)
        return g.compile()


class EquipmentProbe(_KindProbe):
    name = "equipment"
    kind = "equipment_status"
    kinds = ("equipment_status",)
    description = "미달 라인의 설비 가동 상태를 확인한다"


class ProductionProbe(_KindProbe):
    name = "production"
    kind = "production"
    kinds = ("production",)
    description = "미달 라인의 생산 실적을 확인한다"
```

`KpiCheck`의 `required_kinds`를 넓히고 `build_process`를 더한다:

```python
    # probe가 여는 것까지 선언한다. "이 분석이 건드릴 수 있는 전부"를 적는
    # 기존 규약이 그대로 이어지고, 부팅 검증이 별도 규칙 없이 적용된다.
    required_kinds = ("kpi", "equipment_status", "production")

    def build_process(self):
        return ProcessGraph(self, (EquipmentProbe(), ProductionProbe())).compile()
```

- [ ] **Step 5: config를 켠다**

`config/gbm/mx.json`의 `kpi.check` 블록에 추가:

```json
    "kpi.check": {
      "enabled": true,
      "warn_gap_pct": -3.0,
      "critical_gap_pct": -8.0,
      "use_llm_judge": true,
      "max_probe_rounds": 2,
      "llm": { "model": "fake-judge" }
    },
```

- [ ] **Step 6: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_kpi_probes.py -v`
Expected: PASS (8건)

Run: `.venv/bin/python -m pytest && .venv/bin/python -m unittest discover -s tests -t .`
Expected: 둘 다 통과. `tests/test_graph_behaviour.py`와 `tests/test_boot_validation.py`가 손대지 않고 통과해야 한다.

- [ ] **Step 7: 실제로 돌려서 눈으로 확인한다**

```bash
.venv/bin/python -m src run --gbm mx --factory gumi --as-of 2026-08-13T08:00 --stream
```

Expected: `analyze_query` 뒤에 서브그래프들이 실행되고, 리포트의 KPI 섹션이 정상 생성된다. `⚠ 가드레일:` 줄에는 기존의 `kpi.check: 근거 [...::hallucinated]가 입력에 없어 판정을 폐기했습니다`가 그대로 보인다 — `_decide_raw`는 잘못된 목적지를 내지 않으므로 목적지 관련 경고는 나오지 않는 것이 정상이다.

**관찰한 것을 보고서에 적는다:** probe가 몇 라운드 돌았는지, 어느 probe가 선택됐는지, KPI 섹션의 판정 근거에 `equipment_status`나 `production`의 record id가 섞여 들어왔는지 (FakeLLMAdapter._judge_raw는 allowed_ids[:2]만 인용하므로 base KPI id만 나오는 것이 정상이며, 그 사실을 그대로 적는다). **추가 조회가 실제로 몇 번 일어나는지가 B-2로 갈지를 판단하는 근거다.**

- [ ] **Step 8: 커밋**

```bash
git add src/application/subgraphs/kpi/check.py config/gbm/mx.json tests/test_kpi_probes.py
git commit -m "Let kpi.check look at equipment and output before judging

Splitting process into fetch, compute and judge lets judge re-run over
whatever the probes have added. Metrics stay derived from the base KPI
records alone, so a probe can change how a number is interpreted but
never the number itself.

required_kinds now names the kinds the probes open, which is what makes
boot validation cover them without a second rule."
```

---

## 자체 검토

**스펙 대조** — 설계 문서의 각 절이 어느 태스크에 들어갔는지.

| 스펙 절 | 태스크 |
|---|---|
| 4 `process` 슬롯 일반화, `_run_nested` | 1 |
| 5 `Probe` | 3, 6 |
| 6-1 반복 상한 | 1(필드), 4(동작) |
| 6-2 목적지 허용목록 | 3(조립 검사), 4(실행 후보) |
| 6-3 근거 허용집합 누적 | 3, 6 |
| 6-4 궤적 기록(신규 없음) | 4(프롬프트 차이 보증) |
| 7 상태 모델, 판정 교체 | 1(필드), 3(교체), 6(적용) |
| 7 `generate_output` drops 병합 | 1 |
| 8 `decide_next`, `ProbeDecision`, `LLMPort.decide` | 2, 3 |
| 9 probe 실패 격리 | 3(구현), 5(보증) |
| 10 config | 6 |
| 12 테스트 전략 | 각 태스크에 분산 |

빠진 항목 없음. 스펙 13절의 미결(실측 근거 부재)은 Task 6 Step 7에서 관찰값을 남기는 것으로 이어진다.

**타입 일관성** — `SubgraphState`의 신규 필드명(`probe_records`, `probe_rounds`, `probed`)이 Task 1 정의부터 Task 6 테스트까지 동일하다. `Probe`의 `name`/`kinds`/`description`/`compile(deps, config)`이 Task 3 정의와 Task 6 구현에서 같다. `DONE = "done"`이 `process_graph.py`와 `llm.py` 양쪽에 있는데, 어댑터가 허용목록만 보고 판단하므로 값이 같기만 하면 되고 서로를 import하지 않는다 — 상수 하나로 합치면 도메인이 인프라를 import하게 되므로 그대로 둔다.

**Task 4·5의 성격** — 이 둘은 새 코드가 아니라 Task 3 동작의 계약을 테스트로 고정한다. Step 2에서 처음부터 통과할 수 있고, 그 경우 Step 3을 건너뛴다. TDD의 형태는 아니지만, 상한과 실패 격리는 **회귀했을 때 조용히 깨지는** 성질이라 독립된 게이트를 둘 값이 있다.

**위험 지점** — `kpi.check`의 기존 `process` 메서드를 지우므로, `config`에서 `nodes.process`로 `kpi.check`를 가리키는 곳이 있으면 깨진다. 현재 그런 config는 없다(`stock_gumi`만 `material.stock`을 가리킨다). Task 6 Step 6의 `test_boot_validation.py` 통과가 이 신호다.
