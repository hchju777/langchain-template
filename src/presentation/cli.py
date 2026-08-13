"""controller — 인자를 BaseContext로 바꿔 유스케이스를 호출한다.

CLI가 presentation인 이유: 사람이 명령을 넣고 사람이 결과를 보는 면이기
때문이다. 반대로 스케줄러는 같은 유스케이스를 부르지만 사람이 없는 시간
트리거라 infrastructure에 둔다.

어느 쪽이든 **유스케이스 자체는 아니다.** 인자 파싱과 표시만 하고 실제
일은 그래프가 한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from src.application.graph.builder import build_dependencies, build_graph
from src.application.graph.state import ReportState
from src.config.env import EnvConfig
from src.config.loader import DeployConfig
from src.config.registry import (
    ConfigValidationError,
    all_nodes,
    all_subgraphs,
    discover,
    is_slot_only,
)
from src.constants import KEY_SUBGRAPHS
from src.domain.models import BaseContext, LLMTrace
from src.infrastructure.checkpoint import thread_id_for
from src.infrastructure.llm import replay_map
from src.presentation.renderers import TemplateError


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="app", description="운영 상태 분석 리포트")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="리포트 생성")
    run.add_argument("--gbm")
    run.add_argument("--factory")
    run.add_argument(
        "--as-of",
        help="기준 시각 (ISO8601). 생략하면 현재 시각. 재실행은 이 값을 고정한다.",
    )
    run.add_argument("--stream", action="store_true", help="노드별 진행 표시")
    run.add_argument("--quiet", action="store_true", help="리포트 본문 출력 생략")
    run.add_argument(
        "--save-traces",
        metavar="PATH",
        help="LLM 프롬프트·응답을 JSON으로 저장 (나중에 --replay로 재생)",
    )
    run.add_argument(
        "--replay",
        metavar="PATH",
        help="저장된 응답을 재생. LLM을 고정한 채 후속 로직만 디버깅할 때.",
    )
    run.add_argument(
        "--show-checkpoints",
        action="store_true",
        help="실행 후 체크포인트 목록 표시 (Time Travel 진입점)",
    )

    show = sub.add_parser("config", help="설정 진단")
    show.add_argument("action", choices=["show"])
    show.add_argument("--gbm")
    show.add_argument("--factory")

    sub.add_parser("registry", help="등록된 서브그래프 목록")
    return p


def _resolve(args) -> tuple[str, str, EnvConfig]:
    """배포 좌표를 정한다. CLI 인자가 .env보다 우선한다."""
    env = EnvConfig()
    return (args.gbm or env.gbm), (args.factory or env.factory), env


async def _run(args) -> int:
    gbm, factory, env = _resolve(args)
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now()

    cfg = DeployConfig(gbm, factory)

    replay = None
    if args.replay:
        path = Path(args.replay)
        if not path.exists():
            print(f"\n✗ replay 파일이 없습니다: {path}\n")
            return 2
        saved = [LLMTrace(**t) for t in json.loads(path.read_text(encoding="utf-8"))]
        replay = replay_map(saved)

    # 부팅 검증은 전부 여기서 끝난다. 템플릿 로드(build_dependencies)와
    # config·레지스트리 대조(build_graph)가 모두 이 블록 안에 있어야
    # 사용자가 raw traceback 대신 안내를 본다.
    try:
        deps = build_dependencies(cfg, env, replay=replay)
        graph = build_graph(cfg, deps, env, replay=replay)
    except (ConfigValidationError, TemplateError, ValueError, KeyError) as exc:
        print(f"\n✗ {exc}\n")
        return 2

    ctx = BaseContext(as_of=as_of, gbm=gbm, factory=factory)
    initial = ReportState(ctx=ctx)

    # 같은 (gbm, factory, as_of)는 같은 스레드. 재개할 때 무엇을 이어받을지가
    # 자명해지고, 과거 실행을 날짜로 찾아갈 수 있다.
    thread_id = thread_id_for(gbm, factory, as_of)
    run_config = {"configurable": {"thread_id": thread_id}}

    print(f"▶ {gbm}/{factory} · as_of={as_of.isoformat(timespec='seconds')}")
    if replay:
        print(f"  replay: {len(replay)}건의 저장된 응답을 재생합니다")

    try:
        if args.stream:
            # updates로 진행을 표시하면서 values로 최종 State를 받는다.
            # 그래프를 두 번 돌리면 발송이 두 번 나간다.
            state = {}
            async for mode, chunk in graph.astream(
                initial, stream_mode=["updates", "values"], config=run_config
            ):
                if mode == "updates":
                    for node in chunk:
                        print(f"  · {node}")
                else:
                    state = chunk
        else:
            state = await graph.ainvoke(initial, config=run_config)
    finally:
        await deps.close()

    rendered = state.get("rendered") or ""
    if not args.quiet:
        print()
        print(rendered)

    for record in state.get("delivered", []):
        print(f"✓ 발송[{record.channel}] → {record.target}")

    # 서브그래프마다 다른 LLM을 쓸 수 있으므로 어댑터가 아니라 State에서 읽는다.
    for drop in state.get("guardrail_drops", []):
        print(f"⚠ 가드레일: {drop}")

    errors = state.get("errors", [])
    for err in errors:
        print(f"⚠ 부분 실패: {err.key}.{err.slot} — {err.message}")

    traces = state.get("traces", [])

    if args.save_traces:
        path = Path(args.save_traces)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([t.model_dump() for t in traces], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"✓ trace {len(traces)}건 저장 → {path}")

    if args.show_checkpoints:
        await _print_checkpoints(graph, run_config)

    replayed = sum(1 for t in traces if t.replayed)
    summary = f"\nLLM 호출 {len(traces)}건"
    if replayed:
        summary += f" (재생 {replayed}건)"
    summary += f" · 서브그래프 {len(state.get('sections', []))}건"
    print(summary)
    return 0


async def _print_checkpoints(graph, run_config: dict) -> None:
    """체크포인트 목록. Time Travel은 여기서 checkpoint_id를 골라 시작한다."""
    thread_id = run_config["configurable"]["thread_id"]
    print(f"\n체크포인트 (thread_id={thread_id})")

    snapshots = [s async for s in graph.aget_state_history(run_config)]
    if not snapshots:
        print("  없음 — checkpoint.backend가 'none'인지 확인하세요")
        return

    # 오래된 것부터 보여준다. 실행 순서와 같아 읽기 쉽다.
    for snap in reversed(snapshots):
        step = snap.metadata.get("step", "?") if snap.metadata else "?"
        nodes = ", ".join(snap.next) if snap.next else "(완료)"
        cid = snap.config["configurable"].get("checkpoint_id", "?")
        print(f"  step {step:>2}  다음: {nodes:24} {cid}")

    print(
        f"\n  총 {len(snapshots)}개. 특정 시점 State를 보거나 값을 바꿔 다시 돌리려면\n"
        "  graph.aget_state(...) / aupdate_state(...)에 checkpoint_id를 넘기세요."
    )


def _config_show(args) -> int:
    gbm, factory, _ = _resolve(args)
    cfg = DeployConfig(gbm, factory)

    print(f"# 병합 결과 — {gbm}/{factory}\n")
    print("## 계층 (뒤가 앞을 덮어씀)")
    for layer, path in cfg.layer_files.items():
        mark = "○" if path.exists() else "×"
        print(f"  {mark} {layer:16} {path}")

    print("\n## 값의 출처")
    for key in sorted(cfg.origins):
        print(f"  {key:52} ← {cfg.origins[key]}")

    print("\n## 병합된 config")
    print(json.dumps(cfg.data, indent=2, ensure_ascii=False))
    return 0


def _registry_list() -> int:
    discover()
    subs = all_subgraphs()
    nodes = all_nodes()
    print(f"등록된 서브그래프 {len(subs)}건 (이름은 파일 경로에서 유도됨)\n")
    for name, cls in sorted(subs.items()):
        module = cls.__module__.replace("src.application.subgraphs.", "")
        tag = "  [슬롯 전용]" if is_slot_only(name) else ""
        print(f"  {name:24} {cls.title:22} ← {module}.py{tag}")
        kinds = getattr(cls, "required_kinds", ())
        if kinds:
            print(f"  {'':24} 요청 데이터: {', '.join(kinds)}")

    if nodes:
        print(f"\n공유 노드 부품 {len(nodes)}건 (nodes 슬롯 override 대상)\n")
        for name in sorted(nodes):
            print(f"  {name}")
    print(
        "\n슬롯 override에는 위 둘 다 쓸 수 있습니다: "
        '{"nodes": {"process": "<이름>"}}'
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    args = _parser().parse_args(argv)

    if args.command == "run":
        return asyncio.run(_run(args))
    if args.command == "config":
        return _config_show(args)
    if args.command == "registry":
        return _registry_list()
    return 1
