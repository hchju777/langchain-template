"""중복 실행 락.

fcntl은 Windows에 없어 쓸 수 없다. 원자적 파일 생성(O_CREAT|O_EXCL)으로
두 OS에서 같은 코드가 돈다.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from src.infrastructure.lock import LockBusyError, RunLock

KEY = "mx:gumi:20260813T0800"
NOW = datetime(2026, 8, 13, 8, 0)


class LockTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def lock(self, key=KEY, now=NOW, **kw):
        return RunLock(key, self.root, now=now, **kw)

    # ------------------------------------------------------------------
    def test_획득하면_락_파일이_생긴다(self):
        with self.lock() as lk:
            self.assertTrue(lk.path.exists())

    def test_빠져나오면_지워진다(self):
        with self.lock() as lk:
            path = lk.path
        self.assertFalse(path.exists())

    def test_두_번째_획득은_막힌다(self):
        first = self.lock()
        first.acquire()
        try:
            with self.assertRaises(LockBusyError):
                self.lock().acquire()
        finally:
            first.release()

    def test_해제_후에는_다시_잡을_수_있다(self):
        a = self.lock()
        a.acquire()
        a.release()
        b = self.lock()
        b.acquire()  # 예외가 나면 실패
        b.release()

    def test_다른_키는_서로_막지_않는다(self):
        a = self.lock("mx:gumi:20260813T0800")
        b = self.lock("mx:asan:20260813T0800")
        a.acquire()
        b.acquire()  # 서로 무관해야 한다
        a.release()
        b.release()

    def test_예외가_나도_락이_풀린다(self):
        try:
            with self.lock() as lk:
                path = lk.path
                raise RuntimeError("실행 중 실패")
        except RuntimeError:
            pass
        self.assertFalse(path.exists(), "실패해도 다음 실행을 막으면 안 된다")

    # ------------------------------------------------------------------
    def test_락_파일에_pid와_시각이_남는다(self):
        with self.lock() as lk:
            info = lk.holder()
        self.assertEqual(info["pid"], os.getpid())
        self.assertEqual(info["key"], KEY)
        self.assertIn("2026-08-13", info["acquired_at"])

    def test_에러_메시지에_누가_쥐고_있는지_담긴다(self):
        first = self.lock()
        first.acquire()
        try:
            with self.assertRaises(LockBusyError) as ctx:
                self.lock().acquire()
            message = str(ctx.exception)
            self.assertIn(str(os.getpid()), message)
            self.assertIn(KEY, message)
        finally:
            first.release()

    # ------------------------------------------------------------------
    def test_오래된_락은_강탈한다(self):
        """프로세스가 죽으면서 락을 못 지운 경우를 풀어야 한다."""
        stale = self.lock(now=NOW - timedelta(hours=12))
        stale.acquire()

        fresh = self.lock(now=NOW, stale_after=timedelta(hours=6))
        fresh.acquire()  # 12시간 전 락이므로 강탈
        self.assertEqual(fresh.holder()["acquired_at"], NOW.isoformat(timespec="seconds"))
        fresh.release()

    def test_아직_유효한_락은_강탈하지_않는다(self):
        recent = self.lock(now=NOW - timedelta(minutes=30))
        recent.acquire()
        try:
            with self.assertRaises(LockBusyError):
                self.lock(now=NOW, stale_after=timedelta(hours=6)).acquire()
        finally:
            recent.release()

    def test_깨진_락_파일은_강탈한다(self):
        broken = self.lock()
        broken.root.mkdir(parents=True, exist_ok=True)
        broken.path.write_text("이건 JSON이 아님", encoding="utf-8")

        lk = self.lock()
        lk.acquire()  # 읽을 수 없는 락은 쓰레기로 본다
        self.assertEqual(lk.holder()["pid"], os.getpid())
        lk.release()

    # ------------------------------------------------------------------
    def test_키의_콜론이_파일명에서_치환된다(self):
        """Windows 파일명에 콜론을 쓸 수 없다."""
        lk = self.lock()
        self.assertNotIn(":", lk.path.name)
        self.assertIn("mx_gumi_20260813T0800", lk.path.name)

    def test_락_디렉터리가_없어도_만든다(self):
        nested = RunLock(KEY, self.root / "a" / "b", now=NOW)
        nested.acquire()
        self.assertTrue(nested.path.exists())
        nested.release()


if __name__ == "__main__":
    unittest.main()
