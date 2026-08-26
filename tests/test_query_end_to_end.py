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

    def test_attachment_alone_forks_the_run(self):
        """--query 없이 --context만 준 실행.

        재현된 사고: 첨부 문서가 내용을 바꿨는데도 정규 실행과 thread_id를
        공유해, 체크포인터가 영속이면 임시 실행의 발송 기록이 정규 스레드에
        올라앉아 새벽 발송을 통째로 막을 수 있었다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.md"
            path.write_text("# 정비 계획\n오늘 A라인 점검", encoding="utf-8")
            run = _run(context_paths=[str(path)])

        self.assertNotEqual(run.thread_id, SCHEDULED_THREAD)
        self.assertTrue(run.thread_id.startswith(f"{SCHEDULED_THREAD}:q"))
        # 범위는 그대로다 — 문서를 붙였다고 분석이 좁아지면 안 된다.
        self.assertTrue(run.state["requirement"].is_full_scope)
        self.assertEqual([d.id for d in run.state["references"]], ["attach-01"])

    def test_attachment_marks_the_context_ad_hoc(self):
        """발송 범위 판단이 보는 값이 실제로 켜져야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.md"
            path.write_text("# 정비 계획\n본문", encoding="utf-8")
            run = _run(context_paths=[str(path)])
        ctx = run.state["ctx"]
        self.assertTrue(ctx.is_ad_hoc)
        self.assertEqual(ctx.attachments, (str(Path(path).resolve()),))

    def test_scheduled_context_is_not_ad_hoc(self):
        """스케줄러 실행은 사람 입력이 없으므로 정규 실행 그대로여야 한다."""
        run = _run()
        self.assertFalse(run.state["ctx"].is_ad_hoc)
        self.assertEqual(run.state["ctx"].attachments, ())
