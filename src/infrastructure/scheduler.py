"""상주 스케줄러 — cron으로 리포트를 돌린다.

infrastructure인 이유: 사람 없는 시간 트리거이고, APScheduler라는 특정
라이브러리에 붙어 있기 때문이다. "매일 8시"라는 **정책**은 config 데이터이고,
cron 파싱·타이머·콜백 디스패치는 여기 기술 세부사항이다.

그리고 **유스케이스가 아니다.** 시간이 되면 `run_report`를 부를 뿐이다 —
CLI가 하는 일과 똑같고, 그래서 둘이 같은 함수를 공유한다.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.application.usecase import run_report
from src.config.env import EnvConfig
from src.config.loader import DeployConfig
from src.constants import LOCK_ROOT
from src.infrastructure.checkpoint import thread_id_for
from src.infrastructure.lock import LockBusyError, RunLock

logger = logging.getLogger(__name__)

DEFAULT_CRON = "0 8 * * *"
DEFAULT_TIMEZONE = "Asia/Seoul"


#: 요일 이름. 숫자 대신 이걸 쓰면 표준 cron과의 차이를 겪지 않는다.
DOW_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class CronWeekdayWarning(UserWarning):
    """요일을 숫자로 적었을 때. 표준 cron과 의미가 다르다."""


def _warn_numeric_weekday(expression: str) -> None:
    """APScheduler의 요일 숫자는 표준 cron과 다르다.

        표준 cron(Unix)  0=일 1=월 2=화 ... 6=토
        APScheduler      0=월 1=화 2=수 ... 6=일

    그래서 "0 8 * * 1"은 crontab에서는 월요일이지만 여기서는 **화요일**이다.
    자동 변환은 하지 않는다 — "*/2"나 "1-5/2"처럼 스텝이 섞이면 변환 규칙이
    금방 지저분해지고, APScheduler 문서와 동작이 달라져 오히려 헷갈린다.
    대신 경고해서 이름(mon~sun)을 쓰도록 유도한다.
    """
    fields = expression.split()
    if len(fields) < 5:
        return
    dow = fields[4]
    if dow == "*" or not any(ch.isdigit() for ch in dow):
        return
    logger.warning(
        "cron '%s'의 요일 '%s'이 숫자입니다. APScheduler는 0=월요일이라 "
        "표준 cron(0=일요일)과 하루씩 어긋납니다. "
        "이름으로 쓰는 편이 안전합니다 (예: 'mon-fri', 'sun').",
        expression,
        dow,
    )


def parse_cron(expression: str, timezone: str) -> CronTrigger:
    """표준 5필드 cron을 트리거로.

    APScheduler가 파싱과 다음 실행 시각 계산을 맡는다. 직접 만들면
    서머타임·월말 같은 경계에서 틀리기 쉽다.

    **요일 숫자는 표준 cron과 다르다** — `_warn_numeric_weekday` 참조.
    """
    _warn_numeric_weekday(expression)
    return CronTrigger.from_crontab(expression, timezone=timezone)


async def run_once(
    gbm: str,
    factory: str,
    as_of: datetime | None = None,
    *,
    env: EnvConfig | None = None,
    lock_root: Path | None = None,
    config_root: Path | None = None,
) -> bool:
    """한 번 실행한다. 락을 잡지 못하면 조용히 건너뛴다.

    돌려주는 값은 "실제로 실행했는가"다. 스케줄러가 로그에 쓴다.
    """
    as_of = as_of or datetime.now()
    key = thread_id_for(gbm, factory, as_of)
    lock = RunLock(key, lock_root or LOCK_ROOT)

    try:
        lock.acquire()
    except LockBusyError as exc:
        logger.warning("건너뜀: %s", exc)
        return False

    try:
        run = await run_report(
            gbm, factory, as_of, env=env, config_root=config_root
        )
    finally:
        lock.release()

    logger.info(
        "완료 thread=%s 섹션=%d 실패=%d 발송=%d",
        run.thread_id,
        len(run.sections),
        len(run.errors),
        len(run.delivered),
    )
    for drop in run.guardrail_drops:
        logger.warning("가드레일: %s", drop)
    return True


def build_scheduler(
    gbm: str,
    factory: str,
    *,
    env: EnvConfig | None = None,
    lock_root: Path | None = None,
    config_root: Path | None = None,
) -> tuple[AsyncIOScheduler, str, str]:
    """config의 schedule 블록으로 스케줄러를 구성한다."""
    cfg = DeployConfig(gbm, factory, root=config_root)
    schedule = cfg.section("schedule")
    expression = schedule.get("cron", DEFAULT_CRON)
    timezone = schedule.get("timezone", DEFAULT_TIMEZONE)

    scheduler = AsyncIOScheduler(timezone=timezone)
    scheduler.add_job(
        run_once,
        trigger=parse_cron(expression, timezone),
        kwargs={
            "gbm": gbm,
            "factory": factory,
            "env": env,
            "lock_root": lock_root,
            "config_root": config_root,
        },
        id=f"report:{gbm}:{factory}",
        # 이전 실행이 안 끝났으면 새로 시작하지 않는다. 파일 락과 이중으로
        # 막는 셈인데, 이쪽은 같은 프로세스 안, 락은 프로세스 간을 본다.
        max_instances=1,
        # 프로세스가 잠깐 죽었다 살아난 경우 놓친 실행을 한 번만 따라잡는다.
        coalesce=True,
        misfire_grace_time=3600,
    )
    return scheduler, expression, timezone


def next_runs(trigger: CronTrigger, count: int = 5, now: datetime | None = None) -> list[datetime]:
    """다음 실행 시각들. 설정이 의도대로 읽혔는지 눈으로 확인할 때 쓴다."""
    out: list[datetime] = []
    prev = now
    for _ in range(count):
        nxt = trigger.get_next_fire_time(prev, prev or datetime.now(trigger.timezone))
        if nxt is None:
            break
        out.append(nxt)
        prev = nxt
    return out


def describe(expression: str, timezone: str, count: int = 3) -> str:
    """사람이 읽는 스케줄 요약."""
    upcoming = next_runs(parse_cron(expression, timezone), count)
    lines = [f"cron '{expression}' ({timezone})"]
    lines += [f"  다음 실행: {t:%Y-%m-%d %H:%M:%S %Z}" for t in upcoming]
    return "\n".join(lines)
