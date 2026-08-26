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


def _run(query, channels, attachments=()):
    deps = Dependencies(deliveries=channels)
    ctx = BaseContext(
        as_of=AS_OF,
        gbm="mx",
        factory="gumi",
        query=query,
        attachments=tuple(attachments),
    )
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

    def test_attachment_only_run_skips_broadcast_channels(self):
        """--query 없이 --context만 준 실행도 사람이 만든 임시 산출물이다.

        재현된 사고: 첨부 문서가 내용을 바꾼 리포트가 정규 수신자에게 메일로
        나갔다.
        """
        file_ch, mail_ch = Spy("file", False), Spy("mail", True)
        out = _run(None, [file_ch, mail_ch], attachments=["/docs/plan.md"])
        self.assertEqual([d.channel for d in out["delivered"]], ["file"])
        self.assertEqual(mail_ch.keys, [])

    def test_attachment_only_run_uses_a_distinct_idempotency_key(self):
        """재현된 사고: 첨부 문서 실행이 정규 리포트 파일을 그대로 덮어썼다."""
        file_ch = Spy("file", False)
        _run(None, [file_ch], attachments=["/docs/plan.md"])
        self.assertNotEqual(file_ch.keys[0], SCHEDULED_KEY)
        self.assertTrue(file_ch.keys[0].startswith(f"{SCHEDULED_KEY}_q"))

    def test_attachment_changes_the_key_of_a_query_run(self):
        """같은 질의라도 붙인 문서가 다르면 다른 산출물이다."""
        bare, attached = Spy("file", False), Spy("file", False)
        _run("재고만", [bare])
        _run("재고만", [attached], attachments=["/docs/plan.md"])
        self.assertNotEqual(bare.keys, attached.keys)

    def test_same_attachments_reuse_the_same_key(self):
        a, b = Spy("file", False), Spy("file", False)
        _run(None, [a], attachments=["/docs/a.md", "/docs/b.md"])
        _run(None, [b], attachments=["/docs/b.md", "/docs/a.md"])
        self.assertEqual(a.keys, b.keys)

    def test_shipped_channels_declare_broadcast(self):
        self.assertFalse(FileDelivery.broadcast)
        self.assertTrue(MailDelivery.broadcast)
