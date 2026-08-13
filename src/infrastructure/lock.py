"""중복 실행 락.

막으려는 상황이 둘이다.

    상주 프로세스가 둘 이상 떴다 (롤링 배포 중 겹침, HA 이중화)
    이전 실행이 다음 스케줄을 넘겨 아직 돌고 있다

`fcntl.flock`은 Windows에 없다. 대신 **원자적 파일 생성**(O_CREAT|O_EXCL)을
쓴다 — 두 OS 모두에서 원자적이고 표준 라이브러리만 필요하다.

락 파일에 PID와 획득 시각을 적어두고, 너무 오래된 락은 강탈한다. 프로세스가
죽으면서 락을 못 지운 경우를 풀기 위해서다. PID 생존 확인은 OS마다 달라
쓰지 않고 시간만 본다 — 하루 한 번 도는 배치에는 그걸로 충분하다.

Mongo를 쓴다면 `{gbm, factory, as_of}` 유니크 인덱스가 더 낫다. 여러 호스트에
걸쳐 동작하기 때문이다. 파일 락은 **같은 호스트 안에서만** 유효하다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

#: 이 시간이 지난 락은 죽은 프로세스가 남긴 것으로 보고 강탈한다.
DEFAULT_STALE_AFTER = timedelta(hours=6)


class LockBusyError(RuntimeError):
    """다른 프로세스가 같은 실행을 이미 잡고 있을 때."""

    def __init__(self, key: str, holder: dict) -> None:
        self.key = key
        self.holder = holder
        pid = holder.get("pid", "?")
        since = holder.get("acquired_at", "?")
        super().__init__(
            f"'{key}' 실행이 이미 진행 중입니다 (pid={pid}, 시작={since}). "
            "이전 실행이 끝나길 기다리거나, 죽은 프로세스가 남긴 락이면 "
            "락 파일을 지우세요."
        )


class RunLock:
    """한 실행(gbm+factory+as_of)에 대한 배타 락."""

    def __init__(
        self,
        key: str,
        root: Path,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
        now: datetime | None = None,
    ) -> None:
        self.key = key
        self.root = root
        self.stale_after = stale_after
        self._now = now or datetime.now()
        # 키에 콜론이 들어가는데 Windows 파일명에 쓸 수 없다.
        self.path = root / f"{key.replace(':', '_')}.lock"
        self._acquired = False

    # ------------------------------------------------------------------
    def _write(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "key": self.key,
                "pid": os.getpid(),
                "acquired_at": self._now.isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
        )
        # O_EXCL이 핵심 — 이미 있으면 FileExistsError로 실패한다.
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)

    def holder(self) -> dict[str, Any]:
        """현재 락을 쥔 쪽의 정보. 없거나 깨졌으면 빈 dict."""
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _is_stale(self) -> bool:
        info = self.holder()
        raw = info.get("acquired_at")
        if not raw:
            # 내용을 읽을 수 없는 락 파일은 남은 쓰레기로 본다
            return True
        try:
            acquired = datetime.fromisoformat(raw)
        except ValueError:
            return True
        return self._now - acquired > self.stale_after

    # ------------------------------------------------------------------
    def acquire(self) -> None:
        try:
            self._write()
        except FileExistsError:
            if not self._is_stale():
                raise LockBusyError(self.key, self.holder()) from None
            # 오래된 락은 지우고 다시 시도한다. 그 사이 다른 프로세스가
            # 잡았다면 여기서 다시 FileExistsError가 나고 그게 맞다.
            self.path.unlink(missing_ok=True)
            try:
                self._write()
            except FileExistsError:
                raise LockBusyError(self.key, self.holder()) from None
        self._acquired = True

    def release(self) -> None:
        if self._acquired:
            self.path.unlink(missing_ok=True)
            self._acquired = False

    def __enter__(self) -> RunLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
