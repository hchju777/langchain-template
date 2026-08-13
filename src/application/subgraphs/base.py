"""표준 서브그래프 4슬롯.

    validate_input ──→ process ──→ generate_output ──→ (ReportSection)
          │ 실패          │ 실패          │ 실패
          └──────────────┴──────────────┴──→ handle_error ──→ (degraded)

한 서브그래프의 실패가 리포트 전체를 죽이지 않는다. 8시 리포트가 아예
안 오는 것보다 한 섹션이 비어서 오는 게 낫다.

슬롯 실패는 Command로 에러 기록과 라우팅을 한 번에 커밋한다.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from src.constants import SLOT_OUTPUT, SLOT_PROCESS, SLOT_VALIDATE
from src.domain.models import (
    BaseContext,
    HistoricalContext,
    Judgement,
    LLMTrace,
    Metric,
    Record,
    ReportSection,
    Severity,
    SnapshotContext,
    SubgraphError,
)

SLOT_ERROR_NODE = "handle_error"


class SubgraphConfig(BaseModel):
    """모든 서브그래프 config의 공통 부분.

    extra="forbid"가 핵심이다. 이게 없으면 `warn_hourss` 같은 **키 오타가
    조용히 무시되고** 기본값으로 돈다 — 부팅 검증이 잡아야 할 바로 그
    상황을 놓친다.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    window: timedelta | None = None
    cache_ttl: int | None = None


class SubgraphState(BaseModel):
    """서브그래프 내부 State. 부모 State와 분리되어 있다."""

    ctx: BaseContext
    scoped: BaseContext | None = None
    records: list[Record] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    judgements: list[Judgement] = Field(default_factory=list)
    traces: list[LLMTrace] = Field(default_factory=list)
    guardrail_drops: list[str] = Field(default_factory=list)
    section: ReportSection | None = None
    error: SubgraphError | None = None


def guarded(key: str, slot: str, goto_ok: str) -> Callable:
    """슬롯을 감싸 성공/실패 경로를 모두 Command로 결정한다.

    성공 경로까지 Command로 명시하는 이유가 있다. 정적 엣지를 남겨두면
    Command(goto=handle_error)가 그것을 **대체하지 않고 추가로** 동작해서,
    다음 슬롯과 handle_error가 같은 스텝에 함께 실행된다. 둘 다 section을
    쓰므로 충돌한다.

    덤으로 memo의 요구대로 상태 업데이트와 이동이 한 번에 커밋된다.
    """

    def decorator(fn: Callable) -> Callable:
        async def wrapper(state: SubgraphState) -> Any:
            try:
                update = await fn(state)
            except Exception as exc:  # noqa: BLE001 - 슬롯 경계에서 격리한다
                return Command(
                    goto=SLOT_ERROR_NODE,
                    update={
                        "error": SubgraphError(
                            key=key, slot=slot, message=f"{type(exc).__name__}: {exc}"
                        )
                    },
                )
            return Command(goto=goto_ok, update=update)

        wrapper.__name__ = fn.__name__
        return wrapper

    return decorator


class BaseSubgraph:
    """서브그래프의 뼈대. 하위 클래스는 보통 process만 구현하면 된다."""

    registry_name: str = ""
    title: str = ""
    config_model: type[SubgraphConfig] = SubgraphConfig
    #: 이 분석이 스냅샷을 보는지 구간을 보는지. validate_input이 이걸로 입력을 만든다.
    context_type: type[BaseContext] = SnapshotContext
    #: 이 분석이 요청하는 데이터 kind. config의 ports에 매핑이 있어야 하고,
    #: 없으면 부팅 시 잡힌다. 여기 적어두지 않으면 실행 중에야 알게 된다.
    required_kinds: tuple[str, ...] = ()

    def __init__(self, config: SubgraphConfig, deps: Any) -> None:
        self.config = config
        self.deps = deps

    # ------------------------------------------------------------------
    # 슬롯 1: validate_input
    # ------------------------------------------------------------------
    async def validate_input(self, state: SubgraphState) -> dict:
        """공통 BaseContext를 자기 입력 스키마로 확장·검증한다.

        config의 상대 시간("24h")을 as_of 기준 절대 시각 쌍으로 바꾸는 것도
        여기서 한다. config는 읽기 쉽고, 실행은 절대 시각으로 고정된다.
        """
        ctx = state.ctx
        if issubclass(self.context_type, HistoricalContext):
            if self.config.window is None:
                raise ValueError(
                    f"{self.registry_name}는 구간 분석이므로 config에 window가 필요합니다"
                )
            scoped = self.context_type.from_window(ctx, self.config.window)
        else:
            scoped = self.context_type(
                as_of=ctx.as_of, gbm=ctx.gbm, factory=ctx.factory
            )
        return {"scoped": scoped}

    # ------------------------------------------------------------------
    # 슬롯 2: process  (하위 클래스가 구현)
    # ------------------------------------------------------------------
    async def process(self, state: SubgraphState) -> dict:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 슬롯 3: generate_output
    # ------------------------------------------------------------------
    async def generate_output(self, state: SubgraphState) -> dict:
        """세부 요약을 붙여 ReportSection을 완성한다.

        숫자는 이미 metrics에 들어 있고 LLM은 서술만 담당한다.
        """
        severity = max(
            (j.severity for j in state.judgements), default=Severity.NORMAL
        )
        narrative = await self._narrate(state)
        section = ReportSection(
            key=self.registry_name,
            title=self.title or self.registry_name,
            severity=severity,
            narrative=narrative,
            metrics=state.metrics,
            judgements=state.judgements,
        )
        return {
            "section": section,
            "traces": self.deps.llm.drain_traces(),
            "guardrail_drops": self.deps.llm.drain_guardrail_drops(),
        }

    async def _narrate(self, state: SubgraphState) -> str:
        facts = "\n".join(f"- {m.name}: {m.value}{m.unit} {m.note}".rstrip()
                          for m in state.metrics)
        prompt = (
            f"[{self.title}] 아래 사실을 2~3문장으로 요약하세요. "
            f"숫자를 새로 만들지 말고 아래 값만 인용하세요.\n{facts}"
        )
        return await self.deps.llm.narrate(self.registry_name, prompt)

    # ------------------------------------------------------------------
    # 슬롯 4: handle_error
    # ------------------------------------------------------------------
    async def handle_error(self, state: SubgraphState) -> dict:
        err = state.error
        section = ReportSection(
            key=self.registry_name,
            title=self.title or self.registry_name,
            severity=Severity.WARNING,
            narrative=(
                f"이 분석은 `{err.slot}` 단계에서 실패했습니다: {err.message}\n\n"
                "나머지 분석은 정상 수행되었습니다."
            ),
            degraded=True,
        )
        return {"section": section}

    # ------------------------------------------------------------------
    # 조립
    # ------------------------------------------------------------------
    def compile(self):
        """4슬롯을 StateGraph로 엮는다.

        진짜 서브그래프로 만들기 때문에 체크포인터가 슬롯 단위로 상태를
        남기고, Time Travel도 슬롯 단위로 가능하다.
        """
        key = self.registry_name
        g = StateGraph(SubgraphState)
        g.add_node(
            "validate_input",
            guarded(key, SLOT_VALIDATE, "process")(self.validate_input),
            destinations=("process", SLOT_ERROR_NODE),
        )
        g.add_node(
            "process",
            guarded(key, SLOT_PROCESS, "generate_output")(self.process),
            destinations=("generate_output", SLOT_ERROR_NODE),
        )
        g.add_node(
            "generate_output",
            guarded(key, SLOT_OUTPUT, END)(self.generate_output),
            destinations=(END, SLOT_ERROR_NODE),
        )
        g.add_node(SLOT_ERROR_NODE, self.handle_error)

        # 슬롯 간 이동은 전부 Command가 결정한다. 정적 엣지를 두면 실패
        # 경로와 성공 경로가 동시에 열린다.
        g.add_edge(START, "validate_input")
        g.add_edge(SLOT_ERROR_NODE, END)
        return g.compile()
