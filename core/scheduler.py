import random
from datetime import datetime, time, timedelta, timezone
from typing import Awaitable, Callable

from discord.ext import tasks

# 한국시간(KST) 고정 오프셋. 서머타임이 없어서 UTC+9 고정으로 충분하다.
KST = timezone(timedelta(hours=9))

# CLAUDE.md 섹션 3-5: 취침 00:00, 기상 06:30 (2026-08-25 변경 — 기존 08:00에서 앞당김).
SLEEP_START = time(0, 0)
WAKE_TIME = time(6, 30)

_DailyCallback = Callable[[], Awaitable[None]]


def is_sleep_time(now: datetime | None = None) -> bool:
    """지금이 한국시간 취침 시간대(00:00~06:30)인지."""
    current = (now or datetime.now(timezone.utc)).astimezone(KST).time()
    return SLEEP_START <= current < WAKE_TIME


def start_daily(hour: int, minute: int, callback: _DailyCallback) -> tasks.Loop:
    """매일 한국시간 hour:minute에 callback을 한 번 실행하는 백그라운드 태스크를 시작한다.

    부름 이벤트/아침 인사(하루 06:30 기상 시각에 실행)와 취침 이벤트(00:00) 둘 다
    이 함수 위에서 등록한다.
    """
    kst_time = time(hour=hour, minute=minute, tzinfo=KST)
    loop = tasks.loop(time=kst_time)(callback)
    loop.start()
    return loop


def start_interval(seconds: float, callback: _DailyCallback) -> tasks.Loop:
    """seconds 간격으로 callback을 반복 실행하는 백그라운드 태스크를 시작한다.

    부름 이벤트의 "예정 시각이 됐는지" / "만료됐는데 무응답인지" 주기 점검에 쓴다.
    """
    loop = tasks.loop(seconds=seconds)(callback)
    loop.start()
    return loop


def random_times_in_window(count: int, start: time, end: time) -> list[time]:
    """[start, end) 구간 안에서 서로 다른 count개의 시각을 무작위로 뽑아 오름차순으로 반환한다."""
    start_seconds = start.hour * 3600 + start.minute * 60 + start.second
    end_seconds = end.hour * 3600 + end.minute * 60 + end.second
    picks = sorted(random.sample(range(start_seconds, end_seconds), count))
    return [time(hour=s // 3600, minute=(s % 3600) // 60, second=s % 60) for s in picks]
