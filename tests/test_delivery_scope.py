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


def _run(query, channels):
    deps = Dependencies(deliveries=channels)
    ctx = BaseContext(as_of=AS_OF, gbm="mx", factory="gumi", query=query)
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

    def test_shipped_channels_declare_broadcast(self):
        self.assertFalse(FileDelivery.broadcast)
        self.assertTrue(MailDelivery.broadcast)
