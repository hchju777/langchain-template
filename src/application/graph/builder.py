"""Composition Root — config를 보는 유일한 곳.

여기서 config를 풀어 각 노드에 필요한 값만 주입한다. 노드는 config 객체를
들고 다니지 않으므로 테스트가 `node(state)` 호출 한 줄이 된다.
"""

from __future__ import annotations

from dataclasses import replace

from langgraph.graph import END, START, StateGraph

from src.application.decorators import with_cache, with_error_handling, with_timing
from src.application.graph.aggregate import make_aggregate, make_deliver, make_render
from src.application.graph.analyze import ANALYZE_NODE, make_analyze_query
from src.application.graph.state import Dependencies, ReportState
from src.application.subgraphs.base import SubgraphState
from src.config.env import EnvConfig
from src.config.loader import DeployConfig, deep_merge
from src.config.registry import (
    SLOT_KIND_NODE,
    enabled_subgraphs,
    get_subgraph,
    slot_source,
    validate_config,
)
from src.constants import (
    DEFAULT_REPORT_TEMPLATE,
    DEFAULT_TIMEOUT_SEC,
    KEY_CHECKPOINT,
    KEY_DELIVERY,
    KEY_LLM,
    KEY_NODES,
    KEY_PORTS,
    KEY_REPORT,
    KEY_STORES,
    KEY_SUBGRAPHS,
    KEY_TIMEOUT_SEC,
    SLOT_ERROR,
    SLOT_OUTPUT,
    SLOT_PROCESS,
    SLOT_VALIDATE,
)
from src.infrastructure.checkpoint import build_checkpointer
from src.infrastructure.delivery import FileDelivery, MailDelivery
from src.infrastructure.llm import build_llm
from src.infrastructure.router import DataRouter
from src.infrastructure.stores import (
    KafkaAdminAdapter,
    MongoAdapter,
    RedisAdapter,
    RestAdapter,
)
from src.presentation.renderers import MarkdownRenderer


def build_dependencies(
    cfg: DeployConfig,
    env: EnvConfig | None = None,
    replay: dict[str, str] | None = None,
) -> Dependencies:
    """infrastructure와 presentation 어댑터를 만들어 포트에 바인딩한다.

    두 설정이 여기서 만난다:
        cfg (config JSON) — GBM/FCT별 도메인 설정. 임계치, on/off, 템플릿.
        env (.env)        — 배포 환경별 접속 정보. 주소, 계정, 비밀번호.

    어느 노드도 이 둘을 보지 않는다. 조립 시점에 필요한 값만 뽑아 넘긴다.
    """
    env = env or EnvConfig()
    timeout = cfg.get(KEY_STORES, KEY_TIMEOUT_SEC, default=DEFAULT_TIMEOUT_SEC)

    # 실제 구현에서는 env의 접속 정보를 각 어댑터에 넘긴다:
    #   RedisAdapter(url=env.redis_url, password=env.redis_password, timeout=timeout)
    #   MongoAdapter(uri=env.mongodb_uri, password=env.mongodb_password,
    #                database=env.mongodb_database, timeout=timeout)
    #   KafkaAdminAdapter(bootstrap_servers=env.kafka_bootstrap_servers,
    #                     username=env.kafka_username, password=env.kafka_password)
    #   RestAdapter(base_url=env.rest_base_url, token=env.rest_api_token, timeout=timeout)
    adapters = {
        RedisAdapter.name: RedisAdapter(timeout=timeout),
        MongoAdapter.name: MongoAdapter(timeout=timeout),
        KafkaAdminAdapter.name: KafkaAdminAdapter(timeout=timeout),
        RestAdapter.name: RestAdapter(timeout=timeout),
    }

    # config의 ports가 "무엇을 어디서 가져올지"를 정한다.
    # 서브그래프는 이 매핑을 모르고 kind만 요청한다.
    routes = {}
    for kind, adapter_name in cfg.section(KEY_PORTS).items():
        adapter = adapters.get(adapter_name)
        if adapter is None:
            raise ValueError(
                f"config의 '{KEY_PORTS}.{kind}'가 가리키는 어댑터 "
                f"'{adapter_name}'을 찾을 수 없습니다. "
                f"쓸 수 있는 어댑터: {', '.join(sorted(adapters))}"
            )
        # 저장소를 바꾸려면 그쪽 어댑터가 그 kind를 다룰 줄 알아야 한다.
        # 여기서 안 막으면 실행 중에야 알게 된다.
        if kind not in adapter.supported_kinds:
            can = ", ".join(sorted(adapter.supported_kinds)) or "(없음)"
            raise ValueError(
                f"어댑터 '{adapter_name}'은 '{kind}'을(를) 다루지 못합니다. "
                f"이 어댑터가 지원하는 kind: {can}"
            )
        routes[kind] = adapter

    # adapter/model/temperature는 GBM/FCT별로 다를 수 있어 config JSON에서,
    # base_url/api_key는 환경별로 달라지므로 .env에서 온다.
    # 실제 LLM으로 바꾸려면 config에 "adapter": "chat_model" 한 줄이면 된다.
    # replay가 있으면 저장된 응답을 재생한다 — LLM을 고정한 채 후속
    # 로직만 고쳐가며 디버깅할 때 쓴다.
    llm = build_llm(
        cfg.section(KEY_LLM),
        base_url=env.llm_base_url,
        api_key=env.llm_api_key,
        replay=replay,
    )

    deliveries = []
    delivery_cfg = cfg.section(KEY_DELIVERY)
    if delivery_cfg.get("file", {}).get("enabled"):
        deliveries.append(FileDelivery())
    mail_cfg = delivery_cfg.get("mail", {})
    if mail_cfg.get("enabled"):
        # 수신자는 GBM/FCT별이라 config에서, SMTP 접속은 환경별이라 .env에서.
        deliveries.append(
            MailDelivery(
                recipients=mail_cfg.get("recipients", []),
                subject_prefix=mail_cfg.get("subject_prefix", "[운영리포트]"),
                # host=env.smtp_host, port=env.smtp_port,
                # username=env.smtp_username, password=env.smtp_password,
                # sender=env.smtp_sender,
            )
        )

    # 템플릿 교체는 config 한 줄이다. 파일이 없으면 여기서 부팅이 멈춘다.
    report_cfg = cfg.section(KEY_REPORT)
    renderer = MarkdownRenderer(
        template=report_cfg.get("template", DEFAULT_REPORT_TEMPLATE)
    )

    return Dependencies(
        data=DataRouter(routes),
        llm=llm,
        renderer=renderer,
        health=dict(adapters),
        deliveries=deliveries,
        adapters=list(adapters.values()),
    )


def _make_subgraph_node(name: str, compiled):
    """부모 State ↔ 서브그래프 State 변환.

    서브그래프는 자기 State를 쓰므로 부모의 관심사가 새어 들어가지 않는다.
    """

    async def node(state: ReportState) -> dict:
        result = await compiled.ainvoke(
            SubgraphState(ctx=state.ctx, requirement=state.requirement)
        )
        out: dict = {}
        if result.get("section") is not None:
            out["sections"] = [result["section"]]
        if result.get("traces"):
            out["traces"] = result["traces"]
        if result.get("guardrail_drops"):
            out["guardrail_drops"] = result["guardrail_drops"]
        if result.get("error") is not None:
            out["errors"] = [result["error"]]
        return out

    node.__name__ = f"subgraph_{name.replace('.', '_')}"
    return node


def _deps_for(
    deps: Dependencies,
    base_llm_cfg: dict,
    sub_cfg: dict,
    env: EnvConfig,
    replay: dict[str, str] | None = None,
) -> Dependencies:
    """서브그래프 전용 Dependencies. LLM만 갈아끼운다.

    override가 없으면 기본 Dependencies를 그대로 쓴다 — 불필요한 LLM
    인스턴스를 만들지 않기 위해서다.
    """
    override = sub_cfg.get(KEY_LLM)
    if not override:
        return deps
    merged = deep_merge(base_llm_cfg, override)
    return replace(
        deps,
        llm=build_llm(
            merged,
            base_url=env.llm_base_url,
            api_key=env.llm_api_key,
            replay=replay,
        ),
    )


def build_graph(
    cfg: DeployConfig,
    deps: Dependencies,
    env: EnvConfig | None = None,
    replay: dict[str, str] | None = None,
):
    """활성 서브그래프를 fan-out으로 붙이고 취합 → 렌더 → 발송으로 모은다."""
    env = env or EnvConfig()
    subgraph_cfg = cfg.section(KEY_SUBGRAPHS)

    # 부팅 검증. 실패하면 여기서 프로세스가 멈춘다.
    # 라우팅된 kind를 함께 넘겨 "이 분석의 데이터가 어디서 오는지" 미지정도 잡는다.
    routed = deps.data.routed_kinds() if deps.data is not None else None
    parsed = validate_config(subgraph_cfg, routed_kinds=routed)
    active = enabled_subgraphs(subgraph_cfg)
    if not active:
        raise RuntimeError("활성화된 서브그래프가 없습니다. config를 확인하세요.")

    graph = StateGraph(ReportState)
    base_llm_cfg = cfg.section(KEY_LLM)

    for name in active:
        cls = get_subgraph(name)
        # 이 서브그래프만 다른 모델을 쓰고 싶으면 config에 llm 블록을 둔다.
        # 기본 설정 위에 덮어쓰므로 바꿀 키만 적으면 된다.
        #     "kpi.check": { "llm": { "model": "gpt-4o" } }
        sub_deps = _deps_for(deps, base_llm_cfg, subgraph_cfg[name], env, replay)
        instance = cls(parsed[name], sub_deps)

        # 슬롯 override가 있으면 여기서 갈아끼운다 (config 우선).
        # 대상은 공유 노드 부품이거나 다른 서브그래프(보통 상속 클래스)다.
        for slot, target_name in (subgraph_cfg[name].get(KEY_NODES) or {}).items():
            attr = _slot_attr(slot)
            kind, source = slot_source(target_name)
            if kind == SLOT_KIND_NODE:
                bound = source.__get__(instance)
            else:
                # 도너 클래스로 인스턴스를 따로 만든다. 메서드만 빌려오면
                # 그 클래스의 헬퍼·상수에 접근할 수 없다.
                bound = getattr(source(parsed[name], sub_deps), attr)
            setattr(instance, attr, bound)

        node = _make_subgraph_node(name, instance.compile())
        node = with_cache(node, name, parsed[name].cache_ttl)
        node = with_timing(node, name)
        graph.add_node(name, node)
        graph.add_edge(name, "aggregate")    # 전부 끝나야 도는 자연스러운 barrier

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

    graph.add_node("aggregate", with_timing(
        with_error_handling(make_aggregate(deps), "aggregate"), "aggregate"))
    graph.add_node("render", with_timing(make_render(deps), "render"))
    graph.add_node("deliver", with_timing(make_deliver(deps), "deliver"))

    graph.add_edge("aggregate", "render")
    graph.add_edge("render", "deliver")
    graph.add_edge("deliver", END)

    # 체크포인터를 붙이면 재개·Time Travel·fork가 따라온다.
    # 노드도 State도 이 결정을 모른다 — compile()에 넘기는 인자일 뿐이다.
    checkpointer = build_checkpointer(
        cfg.section(KEY_CHECKPOINT), env, extra_types=(ReportState,)
    )
    return graph.compile(checkpointer=checkpointer)


_SLOT_ATTR = {
    SLOT_VALIDATE: "validate_input",
    SLOT_PROCESS: "process",
    SLOT_OUTPUT: "generate_output",
    SLOT_ERROR: "handle_error",
}


def _slot_attr(slot: str) -> str:
    if slot not in _SLOT_ATTR:
        raise KeyError(f"알 수 없는 슬롯 '{slot}'. 가능: {list(_SLOT_ATTR)}")
    return _SLOT_ATTR[slot]


def _route_selected(active: list[str]):
    """State만 보는 순수 함수. LLM 호출은 analyze_query에서 이미 끝났다.

    라우팅 결정이 State에 남아 있으므로 체크포인트로 추적할 수 있다.
    """

    def route(state: ReportState) -> list[str]:
        req = state.requirement
        return list(req.selected) if req and req.selected else list(active)

    return route
