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
import sys
from datetime import datetime
from pathlib import Path

from src.application.usecase import run_report
from src.config.env import EnvConfig
from src.config.loader import DeployConfig
from src.config.registry import (
    ConfigValidationError,
    all_nodes,
    all_subgraphs,
    discover,
    is_slot_only,
)
from src.constants import LOCK_ROOT
from src.domain.models import LLMTrace
from src.infrastructure.checkpoint import CheckpointerUnavailableError, thread_id_for
from src.infrastructure.llm import replay_map
from src.infrastructure.lock import LockBusyError, RunLock
from src.presentation.renderers import TemplateError

#: 부팅 단계에서 날 수 있는 실패. 사용자에게 raw traceback 대신 안내를 보인다.
#: 새 검증을 추가하면 여기에도 넣어야 한다.
BOOT_ERRORS = (
    ConfigValidationError,
    TemplateError,
    CheckpointerUnavailableError,
    ValueError,
    KeyError,
)

# scheduler 모듈은 여기서 import하지 않는다. APScheduler를 끌어오는데,
# 그걸 최상단에서 부르면 스케줄러를 안 쓰는 사람도 패키지가 없으면
# `registry`·`config show`·`run`까지 전부 ImportError로 죽는다.
# `_scheduler()` 안에서 지연 import한다.


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

    sched = sub.add_parser("scheduler", help="상주 모드로 스케줄 실행")
    sched.add_argument("--gbm")
    sched.add_argument("--factory")
    sched.add_argument(
        "--once",
        action="store_true",
        help="스케줄을 기다리지 않고 즉시 한 번만 실행 (배선 확인용)",
    )

    sub.add_parser("registry", help="등록된 서브그래프 목록")
    return p


def _resolve(args) -> tuple[str, str, EnvConfig]:
    """배포 좌표를 정한다. CLI 인자가 .env보다 우선한다."""
    env = EnvConfig()
    return (args.gbm or env.gbm), (args.factory or env.factory), env


async def _run(args) -> int:
    gbm, factory, env = _resolve(args)
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now()

    replay = None
    if args.replay:
        path = Path(args.replay)
        if not path.exists():
            print(f"\n✗ replay 파일이 없습니다: {path}\n")
            return 2
        saved = [LLMTrace(**t) for t in json.loads(path.read_text(encoding="utf-8"))]
        replay = replay_map(saved)

    print(f"▶ {gbm}/{factory} · as_of={as_of.isoformat(timespec='seconds')}")
    if replay:
        print(f"  replay: {len(replay)}건의 저장된 응답을 재생합니다")

    # 중복 실행 방지. 스케줄러와 같은 락을 쓰므로, 배치가 도는 중에 손으로
    # 같은 as_of를 돌리면 여기서 막힌다.
    lock = RunLock(thread_id_for(gbm, factory, as_of), LOCK_ROOT)
    try:
        lock.acquire()
    except LockBusyError as exc:
        print(f"\n✗ {exc}\n")
        return 3

    # 부팅 검증 실패는 여기서 잡는다. 사용자가 raw traceback 대신 안내를 본다.
    try:
        run = await run_report(
            gbm,
            factory,
            as_of,
            env=env,
            replay=replay,
            on_node=(lambda node: print(f"  · {node}")) if args.stream else None,
        )
    except BOOT_ERRORS as exc:
        print(f"\n✗ {exc}\n")
        return 2
    finally:
        lock.release()

    if not args.quiet:
        print()
        print(run.rendered)

    for record in run.delivered:
        print(f"✓ 발송[{record.channel}] → {record.target}")

    # 서브그래프마다 다른 LLM을 쓸 수 있으므로 어댑터가 아니라 State에서 읽는다.
    for drop in run.guardrail_drops:
        print(f"⚠ 가드레일: {drop}")

    for err in run.errors:
        print(f"⚠ 부분 실패: {err.key}.{err.slot} — {err.message}")

    if args.save_traces:
        path = Path(args.save_traces)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [t.model_dump() for t in run.traces], ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        print(f"✓ trace {len(run.traces)}건 저장 → {path}")

    if args.show_checkpoints:
        await _print_checkpoints(run.graph, run.run_config)

    replayed = sum(1 for t in run.traces if t.replayed)
    summary = f"\nLLM 호출 {len(run.traces)}건"
    if replayed:
        summary += f" (재생 {replayed}건)"
    summary += f" · 서브그래프 {len(run.sections)}건"
    print(summary)
    return 0


async def _scheduler(args) -> int:
    """상주 모드. config의 cron으로 리포트를 돌린다."""
    try:
        from src.infrastructure.scheduler import build_scheduler, describe, run_once
    except ImportError as exc:
        print(
            f"\n✗ 스케줄러에는 APScheduler가 필요합니다: {exc}\n"
            "  pip install -r requirements.txt\n"
            "  (다른 명령은 이것 없이도 동작합니다)\n"
        )
        return 2

    gbm, factory, env = _resolve(args)

    try:
        scheduler, expression, timezone = build_scheduler(gbm, factory, env=env)
    except BOOT_ERRORS as exc:
        print(f"\n✗ {exc}\n")
        return 2

    print(f"▶ 스케줄러 시작 — {gbm}/{factory}")
    print(describe(expression, timezone))
    print("  Ctrl+C로 종료합니다.\n")

    if args.once:
        print("  --once: 스케줄을 기다리지 않고 한 번만 실행합니다")
        ran = await run_once(gbm, factory, env=env)
        return 0 if ran else 3

    scheduler.start()
    try:
        # 스케줄러가 백그라운드에서 도는 동안 이벤트 루프를 살려둔다.
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n종료합니다.")
    finally:
        scheduler.shutdown(wait=False)
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


def _force_utf8_stdout() -> None:
    """Windows 콘솔 기본 인코딩(cp949)에서 한글·이모지가 깨지는 걸 막는다.

    리포트에 심각도 표시로 🔴🟡🟢를 쓰는데, cp949로는 인코딩할 수 없어
    UnicodeEncodeError가 난다. 파일로 저장되는 md는 이미 utf-8이라 영향이
    없고, 화면 출력만 문제다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                # 리다이렉트된 스트림 등 재설정할 수 없는 경우는 그냥 둔다
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    args = _parser().parse_args(argv)

    if args.command == "run":
        return asyncio.run(_run(args))
    if args.command == "config":
        return _config_show(args)
    if args.command == "scheduler":
        return asyncio.run(_scheduler(args))
    if args.command == "registry":
        return _registry_list()
    return 1
