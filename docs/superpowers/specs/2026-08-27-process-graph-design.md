# process를 중첩 그래프로 — 설계 (B-1)

작성일: 2026-08-27
대상: langgraph-template
선행 문서: [LLM 자율성 확대 방향 기술 검토](2026-08-26-llm-autonomy-review.md) 8-3절
후속: B-2 (`process` 자리에 ReAct 에이전트) — 이 문서가 짓는 전제 위에 얹는다

---

## 1. 목적과 범위

서브그래프의 `process`를 메서드 하나에서 **중첩 그래프**로 확장한다. 판정 결과에 따라 데이터를 추가로 조회하고 다시 판정하는 흐름을, 실패 격리와 재현성을 유지한 채 표현할 수 있게 한다.

### 포함

- `process` 슬롯 일반화 — 컴파일된 그래프를 꽂을 수 있게 한다
- `Probe` — 추가 조회 단위. 자체가 여러 노드로 된 그래프일 수 있다
- `decide_next` — LLM이 다음 목적지만 고른다
- 가드레일 셋: 반복 상한, probe 목적지 허용목록, 근거 허용집합 누적
- probe 실패가 섹션 전체를 죽이지 않는 격리
- `kpi.check`에 첫 적용

### 제외

- **ReAct 에이전트** — B-2. 이 문서는 "LLM이 목적지를 고른다"까지이고, "순서까지 정한다"는 다음이다
- 나머지 5개 서브그래프의 구조 변경 — 추가 조회할 것이 없다
- 질의·참고 문서 관련 기능 — 프로젝트 A에서 끝났다

### 성공 기준

1. `build_process()`를 구현하지 않은 서브그래프는 **동작이 문자열 단위로 그대로**다. 기존 테스트가 손대지 않고 통과한다.
2. `max_probe_rounds`가 기본값 0이면 probe가 한 번도 돌지 않는다.
3. probe로 가져온 데이터를 인용한 판정이 가드레일에 폐기되지 않는다.
4. probe가 실패해도 그 분석의 본체 판정과 지표는 살아남는다.
5. `--replay`가 지금 그대로 동작한다.

---

## 2. 배경

현재 6개 서브그래프의 `process`는 모두 "fetch → 계산 → (선택적) LLM 판정"이 한 함수에 들어 있다. 실행 중에 얻은 판정을 근거로 **다른 데이터를 더 보는** 흐름을 표현할 자리가 없다.

선행 검토 문서가 정리한 대로, 문제는 자율 판단 자체가 아니라 **궤적이 길어지는 것**이다(τ-bench `pass^k`, METR 80% 지평, self-conditioning). 그래서 이 설계는 자율성을 "목적지 선택"으로 한정하고, 멈출 시점과 조회 대상은 코드가 쥔다. 오류 탐지 정확도가 최고 52.87%인 반면 위치를 알려주면 수정은 잘 한다는 결과(ACL Findings 2024)가 이 역할 분담의 근거다.

현재 조건부 추가 조회를 하는 서브그래프는 **하나도 없다.** 따라서 "상한에 자주 걸릴 것"이라는 예상은 아직 실측 근거가 없으며, 이 설계는 그것을 측정할 수 있게 만드는 것도 목적으로 삼는다.

---

## 3. 아키텍처 변경

```
현재:  validate_input → [process: 메서드 하나] → generate_output
                              │ 실패
                              └→ handle_error (degraded)

변경:  validate_input → [process: 중첩 그래프] → generate_output
                              │ 실패                    ↑
                              └→ handle_error           │
                                                        │
       중첩 그래프 내부:                                 │
         START → fetch → compute → judge → decide_next ─┴─ "done" → END
                                     ↑         │
                                     │         ├─ probe_a (그래프) ─┐
                                     │         └─ probe_b (그래프) ─┤
                                     └─────────────────────────────┘
```

`guarded()`는 **바깥에 그대로 남는다.** 중첩 그래프 안에서 무엇이 터지든 `handle_error`로 가서 degraded 섹션이 되고 리포트 전체는 산다.

---

## 4. `process` 슬롯 일반화

```python
class BaseSubgraph:
    def build_process(self):
        """중첩 그래프를 쓰려면 컴파일된 그래프를 돌려준다.

        None이면 process 메서드를 쓴다. 기존 6개가 전부 이 경로이므로
        이 변경으로 동작이 바뀌지 않는다.
        """
        return None
```

`compile()`이 조립 시점에 한 번 부른다.

```python
    def compile(self):
        nested = self.build_process()
        process = self.process if nested is None else _run_nested(nested)
        g.add_node(
            "process",
            guarded(key, SLOT_PROCESS, "generate_output")(process),
            destinations=("generate_output", SLOT_ERROR_NODE),
        )
```

`stock_gumi`처럼 config `nodes.process`로 메서드를 갈아끼우는 경우는 영향이 없다. 갈아끼운 메서드에는 `build_process`가 없으므로 기본 경로를 탄다.

### 중첩 그래프를 노드로 감싸기

```python
#: 중첩 그래프가 바깥으로 올려보내는 필드. 나머지(ctx, scoped)는 입력이라
#: 다시 쓸 필요가 없다.
_PROCESS_OUTPUT = (
    "records", "probe_records", "metrics", "judgements",
    "probe_rounds", "probed", "guardrail_drops",
)


def _run_nested(compiled):
    async def node(state: SubgraphState) -> dict:
        result = await compiled.ainvoke(state)
        return {k: result[k] for k in _PROCESS_OUTPUT if k in result}

    return node
```

**필드를 골라 올리는 이유가 있다.** `ainvoke`는 최종 상태 **전체**를 돌려준다. 그걸 그대로 `update`로 넘기면 리듀서가 붙은 필드가 두 번 누적된다. 지금 `SubgraphState`에는 리듀서가 없어 당장은 안전하지만, 이 경계는 나중에 누가 리듀서를 붙이는 순간 조용히 중복을 만든다. 명시적으로 고르면 그 함정이 닫힌다.

같은 이유로 **`probe_records`에 리듀서를 붙이지 않는다.** probe 노드가 `{"probe_records": state.probe_records + new}`로 직접 누적한다.

---

## 5. Probe

추가 조회 단위다. 자체가 여러 노드로 된 그래프일 수 있다.

```python
class Probe:
    """추가 조회 한 갈래.

    kinds가 이 probe가 열 수 있는 데이터 종류다. 조립 시점에 서브그래프의
    required_kinds 안에 있는지 검사하므로, 선언하지 않은 곳은 열 수 없다.
    """

    name: str
    kinds: tuple[str, ...]
    #: decide_next 프롬프트에 실린다. 이름만으로는 무엇을 보는 probe인지 모른다.
    description: str

    def compile(self, deps, config):
        """SubgraphState 위에서 도는 컴파일된 그래프를 돌려준다.

        노드는 state를 받으므로 scoped도 state.scoped로 들어온다 — 별도로
        주입하지 않는다. as_of가 상태에 실려 오지 인자로 조작되지 않는다는
        뜻이고, 이것이 시점 재현성의 근거다.
        """
```

`kpi.check`가 쓸 두 개:

```python
class EquipmentProbe(Probe):
    name = "equipment"
    kinds = ("equipment_status",)
    description = "미달 라인의 설비 가동 상태를 확인한다"


class ProductionProbe(Probe):
    name = "production"
    kinds = ("production",)
    description = "미달 라인의 생산 실적을 확인한다"
```

둘 다 스냅샷 kind다. 알람 이력은 구간 데이터라 `SnapshotContext`를 쓰는 `kpi.check`에서는 열 수 없으므로 제외했다 — 6-2절의 컨텍스트 검사가 이것을 부팅에서 막는다. 구간 데이터를 보는 probe가 필요하면 그 분석의 `context_type`이 `HistoricalContext`여야 한다.

probe 내부는 단일 노드여도 되고 `fetch → correlate → summarize`처럼 여러 노드여도 된다. **내부 순서는 코드가 정한다** — 순서까지 LLM이 정하는 것은 B-2다.

probe는 `state.scoped`를 인자로 받는다. `as_of`를 모델이 바꿀 수 없으므로 시점 재현성이 유지된다.

---

## 6. 가드레일 셋

### 6-1. 반복 상한

```python
class SubgraphConfig(BaseModel):
    ...
    #: 추가 조회를 몇 라운드까지 허용할지. 0이면 probe가 아예 돌지 않는다.
    max_probe_rounds: int = 0
```

**기본값 0이 중요하다.** config에서 명시적으로 올려야 동작하므로, 이 기능이 켜졌는지가 config에 드러나고 부팅 검증을 그대로 받는다.

상한 판단은 **LLM에게 묻지 않는다.** `decide_next`가 먼저 코드로 검사하고, 넘었으면 호출 없이 끝낸다.

### 6-2. 목적지 허용목록

probe의 `kinds`가 서브그래프의 `required_kinds` 안에 있는지 **조립 시점에** 검사한다.

```python
class ProcessGraph:
    def __init__(self, subgraph, probes):
        declared = set(subgraph.required_kinds)
        unknown = {k for p in probes for k in p.kinds} - declared
        if unknown:
            raise ValueError(
                f"{subgraph.registry_name}의 probe가 required_kinds에 없는 "
                f"{sorted(unknown)}을(를) 요청합니다. required_kinds에 선언하세요."
            )
```

`required_kinds`는 **base fetch와 probe가 여는 것의 합집합**이다. "이 분석이 건드릴 수 있는 전부"를 선언한다는 기존 규약이 그대로 이어지고, `validate_config`의 데이터 경로 검증이 별도 규칙 없이 적용된다.

**컨텍스트도 같은 자리에서 검사한다.** probe가 요구하는 컨텍스트 종류와 서브그래프의 `context_type`은 결합돼 있다 — 구간 데이터(`alarms`)를 여는 probe를 `SnapshotContext`만 들고 있는 분석에 달면 `state.scoped`에 `start_dt`가 없어 **매 실행 실패한다.** 실패 격리가 있으니 리포트는 정상으로 보이고 아무도 눈치채지 못한다. 그래서 `Probe`가 `context_type`을 선언하고, `kinds`와 나란히 조립 시점에 대조한다.

```python
    context_type: type = SnapshotContext
```

이 검사가 없던 초안으로 구현했을 때 실제로 이 일이 일어났고, 기존 테스트의 순서 의존 단언이 깨지면서야 발각됐다.

실행 시점에는 `decide_next`에 **아직 돌리지 않은 probe 이름만** 넘긴다. 이미 판 곳을 다시 고를 수 없다.

### 6-3. 근거 허용집합 누적

`judge`는 매 라운드 `records + probe_records` 전체의 id를 `allowed_ids`로 넘긴다. 이것이 없으면 probe로 가져온 데이터를 인용한 판정이 통째로 폐기된다.

### 6-4. 궤적 기록은 새로 만들지 않는다

A안에서 비결정적인 것은 `decide_next`의 LLM 호출뿐이다. probe의 fetch는 `scoped`가 고정이라 결정론적이다. 따라서 기존 `LLMTrace` 기록·재생만으로 궤적 전체가 재현되고, **`--replay`가 지금 그대로 동작한다.**

단, `_replay_key`가 `f"{node}|{hash(prompt)}"`이므로 **라운드마다 프롬프트가 달라야 한다.** 같은 프롬프트가 두 번 나오면 replay가 같은 응답을 돌려주어 무한 루프가 된다. 프롬프트에 라운드 번호와 `probed` 목록을 싣는 것이 이 충돌을 막는다.

---

## 7. 상태 모델

```python
class SubgraphState(BaseModel):
    ctx: BaseContext
    scoped: BaseContext | None = None
    requirement: Requirement | None = None     # 프로젝트 A
    records: list[Record] = Field(default_factory=list)
    #: probe가 가져온 것. 명시적으로 누적한다 (리듀서 없음 — 4절 참조).
    probe_records: list[Record] = Field(default_factory=list)
    #: 돈 라운드 수. 상한과 비교한다.
    probe_rounds: int = 0
    #: 이미 돌린 probe 이름. 후보 목록에서 뺀다.
    probed: list[str] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    judgements: list[Judgement] = Field(default_factory=list)
    traces: list[LLMTrace] = Field(default_factory=list)
    guardrail_drops: list[str] = Field(default_factory=list)
    section: ReportSection | None = None
    error: SubgraphError | None = None
```

`records`와 `probe_records`를 나눈 이유는 `compute`가 base만 보게 하기 위해서다. 덕분에 Record에 kind를 태깅할 필요가 없다.

### 판정은 교체한다

루프는 `probe → judge → decide_next`다. `compute`는 base records만 보므로 **한 번만** 돈다. `judge`는 매 라운드 누적 데이터 전체로 판정을 새로 만들어 `judgements`를 **통째로 덮어쓴다.**

누적이 아니라 교체를 택한 이유는, 한 섹션 안에 서로 모순되는 판정이 남는 것이 LLM 호출 한두 번보다 나쁘기 때문이다. 상한이 호출 수를 묶으므로 비용도 예측 가능하다.

### `generate_output`의 guardrail_drops 병합

지금 `generate_output`은 `self.deps.llm.drain_guardrail_drops()`만 돌려준다. probe 실패 기록은 State에 쌓이므로 둘을 합쳐야 한다.

```python
        return {
            "section": section,
            "traces": self.deps.llm.drain_traces(),
            "guardrail_drops": (
                state.guardrail_drops + self.deps.llm.drain_guardrail_drops()
            ),
        }
```

`build_process()`를 안 쓰는 서브그래프는 `state.guardrail_drops`가 빈 목록이라 동작이 바뀌지 않는다.

---

## 8. decide_next

```python
class ProbeDecision(BaseModel):
    """다음에 무엇을 볼지. 목적지 이름만 고른다 — 조회는 코드가 한다."""

    next_step: str
    reason: str
```

`decide_next`는 `ProcessGraph`의 메서드다. `ProcessGraph`가 `deps`·`config`·`probes`를 들고 있으므로 서브그래프 클래스는 이 로직을 몰라도 된다.

```python
class ProcessGraph:
    async def decide_next(self, state: SubgraphState) -> Command:
        remaining = [p.name for p in self.probes if p.name not in state.probed]
        # 상한과 후보 소진은 LLM에게 묻지 않는다. 멈출 시점은 코드가 쥔다.
        if state.probe_rounds >= self.config.max_probe_rounds or not remaining:
            return Command(goto=END)

        decision = await self.deps.llm.decide(
            self.registry_name, self._probe_prompt(state, remaining), [*remaining, "done"]
        )
        if decision.next_step == "done":
            return Command(goto=END)

        return Command(
            goto=f"probe_{decision.next_step}",
            update={
                "probe_rounds": state.probe_rounds + 1,
                "probed": [*state.probed, decision.next_step],
            },
        )
```

`LLMPort`에 `decide()`를 더한다. `plan()`·`judge()`와 같은 단일 경로를 지나므로 기록과 replay가 그대로 적용된다. **허용목록 밖의 값이 오면 `"done"`으로 간주하고 `guardrail_drops`에 남긴다** — 잘못된 선택은 계속 파는 쪽이 아니라 멈추는 쪽으로 넘어져야 한다.

`FakeLLMAdapter._decide_raw`는 남은 후보 중 첫 번째를 고르되, `_judge_raw`·`_plan_raw`와 같은 관례로 **한 번은 목록에 없는 이름을 돌려준다.** 가드레일이 도는지 LLM 없이 확인할 수 있어야 한다.

---

## 9. 에러 처리

**probe 실패는 섹션을 죽이지 않는다.** 선택적 보강 작업이 실패했다고 본체 판정을 버리는 것은 과하다.

```python
def probe_guarded(name: str):
    """probe 실패를 삼키고 decide_next로 돌려보낸다.

    다른 probe를 고르거나 끝내면 된다. base 판정과 지표는 이미 State에 있다.
    """

    def decorator(fn):
        async def wrapper(state: SubgraphState) -> Command:
            try:
                update = await fn(state)
            except Exception as exc:  # noqa: BLE001
                return Command(
                    goto="decide_next",
                    update={"guardrail_drops": [
                        *state.guardrail_drops,
                        f"probe:{name} 실패로 건너뜁니다 ({type(exc).__name__})",
                    ]},
                )
            return Command(goto="judge", update=update)

        return wrapper

    return decorator
```

`fetch`·`compute`·`judge`의 실패는 지금처럼 바깥 `guarded()`가 받아 degraded 섹션이 된다. 그것들은 본체이므로 맞는 동작이다.

실패한 probe도 `probed`에 남으므로 무한히 재시도하지 않는다.

---

## 10. Config

```json
"kpi.check": {
  "enabled": true,
  "warn_gap_pct": -3.0,
  "critical_gap_pct": -8.0,
  "use_llm_judge": true,
  "max_probe_rounds": 2
}
```

`max_probe_rounds`는 `SubgraphConfig`에 있으므로 모든 서브그래프가 받을 수 있고, probe가 없는 서브그래프에서는 아무 일도 하지 않는다.

---

## 11. 변경 파일 목록

| 파일 | 변경 |
|---|---|
| `src/application/subgraphs/base.py` | `build_process()`, `_run_nested`, `SubgraphState` 필드 4개, `SubgraphConfig.max_probe_rounds`, `generate_output`의 drops 병합 |
| `src/application/subgraphs/process_graph.py` | **신규** — `Probe`, `ProcessGraph`, `probe_guarded`, `decide_next` |
| `src/domain/models.py` | `ProbeDecision` |
| `src/domain/ports.py` | `LLMPort.decide()` |
| `src/infrastructure/llm.py` | `decide()`, `_decide_raw()`, 허용목록 검사 |
| `src/application/subgraphs/kpi/check.py` | `build_process()`, probe 두 개, `required_kinds` 확장 |
| `config/gbm/mx.json` | `kpi.check.max_probe_rounds` |
| `tests/test_process_graph.py` | **신규** |
| `tests/test_subgraph_nodes.py` | probe 관련 추가 |

---

## 12. 테스트 전략

기존 방침(어댑터가 아니라 그래프 규약과 순수 로직)을 따르고, 테스트는 `unittest.TestCase`로 쓴다. `pytest`와 `python -m unittest discover -s tests -t .` **둘 다** 통과해야 한다.

**회귀 — 가장 중요하다**

- `build_process()`가 `None`인 5개 서브그래프의 섹션·지표·판정이 그대로다
- `tests/test_graph_behaviour.py`가 **손대지 않고** 통과한다
- `stock_gumi`의 `nodes.process` 교체가 그대로 동작한다

**상한과 허용목록**

- `max_probe_rounds = 0`(기본)이면 probe가 한 번도 안 돌고 `decide_next`가 LLM을 부르지 않는다
- 상한에 도달하면 LLM 호출 없이 끝난다
- 이미 돌린 probe는 후보에서 빠지고, 후보가 비면 끝난다
- 허용목록 밖 이름이 오면 `"done"`으로 처리되고 `guardrail_drops`에 남는다
- probe의 `kinds`가 `required_kinds`를 벗어나면 **조립 시점에** 실패한다

**근거와 누적**

- probe로 늘어난 record를 인용한 판정이 폐기되지 않는다
- `judge`가 매 라운드 판정을 교체하므로 같은 사안의 판정이 중복되지 않는다
- `probe_records`가 라운드마다 누적되고 중복되지 않는다

**실패 격리**

- probe가 예외를 던져도 base 판정과 지표가 살아남고 섹션이 degraded가 아니다
- 실패한 probe는 재시도되지 않는다
- `fetch`·`judge` 실패는 degraded 섹션이 된다

**재현성**

- 같은 응답을 replay하면 같은 probe 궤적이 재생된다
- 라운드마다 프롬프트가 다르다 (replay 키 충돌 방지)

---

## 13. 리스크와 미결

**실측 근거가 없다.** 현재 조건부 조회를 하는 서브그래프가 없으므로, 상한이 2로 충분한지 알 수 없다. `kpi.check`를 굴려 실제 라운드 수를 관찰한 뒤 조정한다. **B-2로 갈지 여부도 이 측정 결과로 판단한다** — 추가 조회가 대부분 0~1회로 끝나면 ReAct가 사줄 것이 적다.

**`judge` 재호출 비용.** 라운드마다 LLM 판정을 다시 하므로 `use_llm_judge`가 켜진 분석은 호출이 최대 (상한+1)배가 된다. 상한이 2면 3배다. 실측 후 상한으로 조절한다.

**probe 내부 순서가 고정이다.** "알람을 보고 그 결과에 따라 정비 이력을 볼지 정한다"는 이 설계로 표현되지 않는다. 그것이 B-2의 존재 이유이며, 여기서는 의도적으로 제외한다.

**`decide_next`가 B-2의 교체 지점이다.** 도구 목록은 `Probe.kinds`가, 상한은 `max_probe_rounds`가, 근거 누적은 `probe_records`가 이미 만들어 둔다. B-2는 `decide_next` 자리에 에이전트를 넣고 상태 변환 한 겹을 더하는 작업이 된다.

---

## 14. 이 설계가 지키는 것

- `build_process()`를 안 쓰면 **동작이 문자열 단위로 동일**하다
- `guarded()`가 바깥에 남아 **분석 단위 실패 격리**가 유지된다
- 조회 대상이 `required_kinds`를 벗어나지 못하므로 **부팅 시점 config 검증**이 계속 작동한다
- LLM 호출이 여전히 어댑터 하나를 지나 `LLMTrace`에 기록되고 **`--replay`가 그대로 동작**한다
- `scoped`가 인자로 전달되어 **`as_of` 재현성**이 유지된다
- 멈출 시점과 조회 대상은 코드가 쥐고, LLM은 목적지만 고른다
