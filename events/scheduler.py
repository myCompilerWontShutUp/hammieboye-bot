import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Awaitable, Callable

from discord.ext import tasks

# 한국시간(KST) 고정 오프셋. 서머타임이 없어서 UTC+9 고정으로 충분하다.
KST = timezone(timedelta(hours=9))

# CLAUDE.md 섹션 3-5: 취침 00:00, 기상 06:30 (2026-08-25 변경 — 기존 08:00에서 앞당김).
SLEEP_START = time(0, 0)
WAKE_TIME = time(6, 30)

# 2026-08-27: 취침 중 맨션 깨움 이벤트(방해금지 모드)가 발동한 밤은, "누가 중간에 깨워서
# 30분 더 잤다"는 컨셉으로 그날의 실제 기상 시각을 07:00로 미룬다(사용자 확정).
DELAYED_WAKE_TIME = time(7, 0)

_DailyCallback = Callable[[], Awaitable[None]]

# 테스트 서버 전용 채널 고정 상태 (2026-08-28, 사용자 확정 — 기존 `hammie awake`/
# `hammie asleep`/`hammie sync` 관리자 명령어를 완전히 대체). `.env`에 넣지 않고 코드에
# 항상 고정값으로 둔다. awake/asleep 채널은 실제 시간과 무관하게 그 상태로 강제되고,
# sync 채널은 실제 시간(is_sleep_time)을 그대로 따른다 — 부름/기상/취침 같은 봇이 스스로
# 올리는 전역 메시지도 이 서버에서는 항상 sync 채널로만 간다(resolve_broadcast_channel_id
# 참고). awake/asleep 채널은 자동 게시 대상이 아니라, 누군가 먼저 말을 걸었을 때만 그
# 고정된 상태로 반응하는 순수 반응형 채널이다.
TEST_GUILD_ID = 1541345080680644651
TEST_AWAKE_CHANNEL_ID = 1541345084493144068
TEST_ASLEEP_CHANNEL_ID = 1542920701214724186
TEST_SYNC_CHANNEL_ID = 1542920725185429677

# 오늘(KST) 밤 방해금지 이벤트가 발동했는지 — 발동한 그 날짜만 기록해두고, 다음 날이 되면
# 날짜가 안 맞아 자연히 무효화된다(별도 리셋 로직 불필요).
_late_wake_date: date | None = None


def mark_late_wake() -> None:
    """취침 중 맨션 깨움 이벤트(방해금지 모드)가 발동했을 때 호출한다 — 오늘(KST)의
    기상 시각을 06:30 대신 07:00로 늦춘다."""
    global _late_wake_date
    _late_wake_date = datetime.now(timezone.utc).astimezone(KST).date()


def is_late_wake_today() -> bool:
    """오늘(KST) 밤 방해금지 이벤트가 발동해서 기상이 07:00로 늦춰진 상태인지."""
    return _late_wake_date == datetime.now(timezone.utc).astimezone(KST).date()


def is_sleep_time(now: datetime | None = None) -> bool:
    """지금이 한국시간 취침 시간대(00:00~06:30, 방해금지 발동 시 00:00~07:00)인지
    — 실제 시간 기준, 관리자 오버라이드 미반영."""
    current_dt = (now or datetime.now(timezone.utc)).astimezone(KST)
    wake_boundary = DELAYED_WAKE_TIME if _late_wake_date == current_dt.date() else WAKE_TIME
    return SLEEP_START <= current_dt.time() < wake_boundary


def is_sleep_time_for(channel_id: int | None = None, now: datetime | None = None) -> bool:
    """이 채널에서 지금이 취침 시간대로 취급돼야 하는지. 테스트 서버(TEST_GUILD_ID)의
    고정 채널(TEST_AWAKE_CHANNEL_ID/TEST_ASLEEP_CHANNEL_ID)이면 실제 시간과 무관하게
    그 상태로 강제되고, 그 외(TEST_SYNC_CHANNEL_ID 포함 나머지 전부)엔 실제 시간
    (is_sleep_time)을 그대로 따른다."""
    if channel_id == TEST_AWAKE_CHANNEL_ID:
        return False
    if channel_id == TEST_ASLEEP_CHANNEL_ID:
        return True
    return is_sleep_time(now)


def resolve_broadcast_channel_id(guild_id: int, last_channel_id: int | None) -> int | None:
    """부름 이벤트/아침 인사/취침 이벤트처럼 봇이 스스로 올리는 전역 메시지가 어느 채널로
    갈지 결정한다. 테스트 서버는 항상 고정된 sync 채널로만 보낸다 — awake/asleep 채널은
    반응형(누군가 먼저 말을 걸었을 때만 동작)이라 자동 게시 대상에서 제외된다. 그 외
    서버는 기존처럼 "마지막 호출된 채널"을 그대로 쓴다."""
    if guild_id == TEST_GUILD_ID:
        return TEST_SYNC_CHANNEL_ID
    return last_channel_id


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
