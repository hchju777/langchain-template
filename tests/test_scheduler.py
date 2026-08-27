"""스케줄러 — cron 파싱, 중복 실행 방지, 유스케이스 공유.

스케줄러는 유스케이스가 아니다. 시간이 되면 CLI와 **같은 함수**를 부른다.
여기 테스트가 그 사실을 붙잡아둔다.
"""

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from src.infrastructure.lock import RunLock
from src.infrastructure.scheduler import (
    DEFAULT_CRON,
    DEFAULT_TIMEZONE,
    build_scheduler,
    describe,
    next_runs,
    parse_cron,
    run_once,
)
from tests.helpers import AS_OF, FACTORY, GBM, base_config, temp_config, with_subgraph_patch


class CronParsingTest(unittest.TestCase):
    """직접 파서를 만들지 않고 APScheduler에 맡긴 부분."""

    def test_매일_정해진_시각(self):
        runs = next_runs(parse_cron("0 8 * * *", "Asia/Seoul"), 3, now=AS_OF)
        self.assertEqual([r.hour for r in runs], [8, 8, 8])
        self.assertEqual([r.minute for r in runs], [0, 0, 0])

    def test_간격_표현식(self):
        runs = next_runs(parse_cron("*/30 * * * *", "UTC"), 3, now=AS_OF)
        gaps = [(b - a).total_seconds() for a, b in zip(runs, runs[1:])]
        self.assertEqual(gaps, [1800.0, 1800.0])

    def test_요일을_이름으로_지정한다(self):
        runs = next_runs(parse_cron("0 8 * * mon", "Asia/Seoul"), 2, now=AS_OF)
        self.assertTrue(all(r.weekday() == 0 for r in runs), "월요일에만 돌아야 한다")

    def test_요일_범위도_이름으로(self):
        runs = next_runs(parse_cron("0 8 * * mon-fri", "Asia/Seoul"), 5, now=AS_OF)
        self.assertTrue(all(r.weekday() < 5 for r in runs), "주말은 빠져야 한다")

    def test_요일_숫자는_표준_cron과_다르다(self):
        """APScheduler는 0=월요일, 표준 cron은 0=일요일.

        자동 변환하지 않으므로 이 차이가 실제로 존재한다는 걸 못 박아둔다.
        crontab에서 월요일인 "1"이 여기서는 화요일이다.
        """
        runs = next_runs(parse_cron("0 8 * * 1", "Asia/Seoul"), 2, now=AS_OF)
        self.assertTrue(
            all(r.weekday() == 1 for r in runs),
            "숫자 1은 (표준 cron의 월요일이 아니라) 화요일이다",
        )

    def test_요일을_숫자로_쓰면_경고한다(self):
        with self.assertLogs("src.infrastructure.scheduler", level="WARNING") as logs:
            parse_cron("0 8 * * 1", "UTC")
        self.assertTrue(any("0=월요일" in m for m in logs.output))

    def test_이름을_쓰면_경고하지_않는다(self):
        import logging

        with self.assertNoLogs("src.infrastructure.scheduler", level="WARNING"):
            parse_cron("0 8 * * mon-fri", "UTC")

    def test_요일이_별이면_경고하지_않는다(self):
        with self.assertNoLogs("src.infrastructure.scheduler", level="WARNING"):
            parse_cron("0 8 * * *", "UTC")

    def test_timezone이_반영된다(self):
        seoul = next_runs(parse_cron("0 8 * * *", "Asia/Seoul"), 1, now=AS_OF)[0]
        utc = next_runs(parse_cron("0 8 * * *", "UTC"), 1, now=AS_OF)[0]
        self.assertNotEqual(seoul.utcoffset(), utc.utcoffset())

    def test_잘못된_표현식은_실패한다(self):
        with self.assertRaises(ValueError):
            parse_cron("이건 cron이 아님", "UTC")

    def test_없는_timezone은_실패한다(self):
        with self.assertRaises(Exception):
            parse_cron("0 8 * * *", "Asia/없는도시")

    def test_describe는_다음_실행을_보여준다(self):
        text = describe("0 8 * * *", "Asia/Seoul", 2)
        self.assertIn("0 8 * * *", text)
        self.assertIn("Asia/Seoul", text)
        self.assertEqual(text.count("다음 실행"), 2)


class BuildSchedulerTest(unittest.TestCase):
    def test_config의_cron을_읽는다(self):
        cfg_data = base_config()
        cfg_data["schedule"] = {"cron": "30 6 * * *", "timezone": "UTC"}
        with temp_config(gbm=cfg_data) as cfg:
            _, expression, timezone = build_scheduler(
                GBM, FACTORY, config_root=cfg._root
            )
        self.assertEqual(expression, "30 6 * * *")
        self.assertEqual(timezone, "UTC")

    def test_schedule_블록이_없으면_기본값을_쓴다(self):
        cfg_data = base_config()
        cfg_data.pop("schedule", None)
        with temp_config(gbm=cfg_data) as cfg:
            _, expression, timezone = build_scheduler(
                GBM, FACTORY, config_root=cfg._root
            )
        self.assertEqual(expression, DEFAULT_CRON)
        self.assertEqual(timezone, DEFAULT_TIMEZONE)

    def test_이전_실행이_안_끝나면_새로_시작하지_않는다(self):
        with temp_config(gbm=base_config()) as cfg:
            scheduler, _, _ = build_scheduler(GBM, FACTORY, config_root=cfg._root)
        job = scheduler.get_jobs()[0]
        self.assertEqual(job.max_instances, 1)
        self.assertTrue(job.coalesce, "놓친 실행은 한 번만 따라잡는다")


class RunOnceTest(unittest.TestCase):
    """스케줄러가 트리거될 때 실제로 하는 일."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.lock_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, cfg_root, as_of=AS_OF):
        return asyncio.run(
            run_once(
                GBM,
                FACTORY,
                as_of,
                lock_root=self.lock_root,
                config_root=cfg_root,
            )
        )

    def test_락을_잡으면_실행한다(self):
        with temp_config(gbm=with_subgraph_patch(lambda c: None)) as cfg:
            self.assertTrue(self._run(cfg._root))

    def test_실행_후_락을_푼다(self):
        with temp_config(gbm=with_subgraph_patch(lambda c: None)) as cfg:
            self._run(cfg._root)
            self.assertTrue(self._run(cfg._root), "두 번째도 돌아야 한다")

    def test_다른_프로세스가_잡고_있으면_건너뛴다(self):
        from src.infrastructure.checkpoint import thread_id_for

        held = RunLock(thread_id_for(GBM, FACTORY, AS_OF), self.lock_root)
        held.acquire()
        try:
            with temp_config(gbm=with_subgraph_patch(lambda c: None)) as cfg:
                self.assertFalse(self._run(cfg._root), "중복 실행을 막아야 한다")
        finally:
            held.release()

    def test_실행이_실패해도_락이_남지_않는다(self):
        def broken(cfg):
            cfg["ports"].pop("material_stock")  # 부팅 검증 실패

        with temp_config(gbm=with_subgraph_patch(broken)) as cfg:
            with self.assertRaises(Exception):
                self._run(cfg._root)

        remaining = list(self.lock_root.glob("*.lock"))
        self.assertEqual(remaining, [], "실패해도 다음 실행을 막으면 안 된다")

    def test_as_of가_다르면_각각_돈다(self):
        with temp_config(gbm=with_subgraph_patch(lambda c: None)) as cfg:
            self.assertTrue(self._run(cfg._root, AS_OF))
            self.assertTrue(self._run(cfg._root, AS_OF - timedelta(days=1)))


class GuardrailOutputTest(unittest.TestCase):
    """같은 가드레일 문구가 여러 줄 찍히는 것은 읽는 사람에게 소음이다.

    judge가 라운드마다 다시 도니 State에는 라운드 수만큼 쌓인다. 몇 번
    폐기됐는지도 기록이므로 State는 그대로 두고, 출력하는 자리에서만 줄인다.
    순서는 처음 나온 대로 지킨다 — 무엇이 먼저 걸렸는지가 단서가 된다.
    """

    DROPS = ["A 폐기", "A 폐기", "B 폐기", "A 폐기"]

    def _fake_run(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            thread_id="t",
            sections=[],
            errors=[],
            delivered=[],
            guardrail_drops=list(self.DROPS),
            traces=[],
            rendered="",
        )

    def test_스케줄러_로그는_중복을_지운다(self):
        from unittest.mock import patch

        import src.infrastructure.scheduler as sched_mod

        run = self._fake_run()

        async def fake_run_report(*args, **kwargs):
            return run

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.object(sched_mod, "run_report", fake_run_report):
            with self.assertLogs(sched_mod.logger, level="WARNING") as caught:
                asyncio.run(
                    sched_mod.run_once(
                        GBM, FACTORY, AS_OF, lock_root=Path(tmp.name)
                    )
                )
        logged = [m.split("가드레일: ")[-1] for m in caught.output if "가드레일" in m]
        self.assertEqual(logged, ["A 폐기", "B 폐기"])

    def test_cli_출력도_중복을_지운다(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch

        from src.presentation import cli as cli_mod

        run = self._fake_run()

        async def fake_run_report(*args, **kwargs):
            return run

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        args = cli_mod._parser().parse_args(
            ["run", "--gbm", GBM, "--factory", FACTORY,
             "--as-of", AS_OF.isoformat(), "--quiet"]
        )
        buffer = io.StringIO()
        with patch.object(cli_mod, "run_report", fake_run_report), \
                patch.object(cli_mod, "LOCK_ROOT", Path(tmp.name)):
            with redirect_stdout(buffer):
                asyncio.run(cli_mod._run(args))
        printed = [ln.split("가드레일: ")[-1]
                   for ln in buffer.getvalue().splitlines() if "가드레일" in ln]
        self.assertEqual(printed, ["A 폐기", "B 폐기"])


class SharedUsecaseTest(unittest.TestCase):
    """CLI와 스케줄러가 같은 함수를 부르는지."""

    def test_스케줄러가_run_report를_쓴다(self):
        import inspect

        from src.infrastructure import scheduler as sched_mod
        from src.presentation import cli as cli_mod

        self.assertIn("run_report", inspect.getsource(sched_mod.run_once))
        self.assertIn("run_report", inspect.getsource(cli_mod._run))


class OptionalDependencyTest(unittest.TestCase):
    """APScheduler는 스케줄러를 쓸 때만 필요하다.

    cli가 최상단에서 scheduler를 import하면 패키지가 없는 환경에서
    registry·config show·run까지 전부 ImportError로 죽는다.
    """

    def test_cli는_apscheduler를_최상단에서_import하지_않는다(self):
        import ast
        import pathlib

        source = pathlib.Path("src/presentation/cli.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level_imports = [
            node.module or ""
            for node in tree.body  # 함수 안이 아니라 모듈 최상단만
            if isinstance(node, ast.ImportFrom)
        ]
        self.assertNotIn(
            "src.infrastructure.scheduler",
            top_level_imports,
            "scheduler는 _scheduler() 안에서 지연 import해야 한다",
        )

    def test_scheduler_모듈만_apscheduler를_필요로_한다(self):
        import pathlib

        for path in pathlib.Path("src").rglob("*.py"):
            if path.name == "scheduler.py":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "import apscheduler",
                text,
                f"{path}가 apscheduler를 직접 import한다",
            )
            self.assertNotIn("from apscheduler", text, f"{path}가 apscheduler를 import한다")


if __name__ == "__main__":
    unittest.main()
