import random
from datetime import datetime, time, timedelta, timezone
from typing import Awaitable, Callable

from discord.ext import tasks

from config import ADMIN_USER_ID

# 한국시간(KST) 고정 오프셋. 서머타임이 없어서 UTC+9 고정으로 충분하다.
KST = timezone(timedelta(hours=9))

# CLAUDE.md 섹션 3-5: 취침 00:00, 기상 06:30 (2026-08-25 변경 — 기존 08:00에서 앞당김).
SLEEP_START = time(0, 0)
WAKE_TIME = time(6, 30)

_DailyCallback = Callable[[], Awaitable[None]]

# 관리자 전용 테스트 오버라이드(hm-awake/hm-asleep/hm-sync, §신규). None이면 실제 시간을
# 그대로 따르고, True/False면 관리자 본인에게만 강제로 취침/기상 상태를 적용한다.
_admin_sleep_override: bool | None = None


def is_sleep_time(now: datetime | None = None) -> bool:
    """지금이 한국시간 취침 시간대(00:00~06:30)인지 — 실제 시간 기준, 오버라이드 미반영."""
    current = (now or datetime.now(timezone.utc)).astimezone(KST).time()
    return SLEEP_START <= current < WAKE_TIME


def set_admin_sleep_override(value: bool | None) -> None:
    """hm-awake(False)/hm-asleep(True)/hm-sync(None)로 설정한다."""
    global _admin_sleep_override
    _admin_sleep_override = value


def get_admin_sleep_override() -> bool | None:
    return _admin_sleep_override


def is_sleep_time_for(user_id: int, now: datetime | None = None) -> bool:
    """이 유저에게 지금이 취침 시간대로 취급돼야 하는지. 관리자 본인이고 hm-awake/hm-asleep
    오버라이드가 걸려있으면 그 값을 그대로 따르고(실제 시간 무시), 그 외엔 항상 실제
    시간(is_sleep_time)을 따른다 — 일반 사용자는 오버라이드의 영향을 받지 않는다(사용자 확정)."""
    if user_id == ADMIN_USER_ID and _admin_sleep_override is not None:
        return _admin_sleep_override
    return is_sleep_time(now)


def format_footer_time(now: datetime) -> str:
    """모든 시스템 임베드(`/내정보`, `/소개`, `/랭킹`)의 footer 공용 포맷. 날짜 + 12시간제
    시각(AM/PM)만 적고, "GMT"/"KST" 같은 시간대 약어는 쓰지 않는다 (사용자 확정)."""
    period = "AM" if now.hour < 12 else "PM"
    return f"{now.strftime('%Y. %m. %d.')} {now.strftime('%I:%M')} {period}"


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


def random_times_in_window(
    count: int, start: time, end: time, min_gap_minutes: int = 0
) -> list[time]:
    """[start, end) 구간 안에서 서로 다른 count개의 시각을 무작위로 뽑아 오름차순으로 반환한다.

    min_gap_minutes > 0이면 인접한 두 시각 사이 간격이 항상 그 값 이상이 되도록 보장한다
    (부름 이벤트 최소 간격 30분, 사용자 확정). 재시도(rejection sampling) 없이, 구간을
    (count-1)*min_gap만큼 줄인 뒤 뽑아서 각 포인트에 순서대로 간격을 더하는 방식으로
    간격을 원천적으로 보장한다.
    """
    start_seconds = start.hour * 3600 + start.minute * 60 + start.second
    end_seconds = end.hour * 3600 + end.minute * 60 + end.second
    min_gap_seconds = min_gap_minutes * 60

    reduced_end = end_seconds - (count - 1) * min_gap_seconds
    if reduced_end <= start_seconds:
        raise ValueError("min_gap_minutes가 너무 커서 구간 안에 count개를 배치할 수 없다")

    picks = sorted(random.sample(range(start_seconds, reduced_end), count))
    adjusted = [p + i * min_gap_seconds for i, p in enumerate(picks)]
    return [time(hour=s // 3600, minute=(s % 3600) // 60, second=s % 60) for s in adjusted]
