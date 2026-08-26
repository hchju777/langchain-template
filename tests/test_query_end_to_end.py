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
