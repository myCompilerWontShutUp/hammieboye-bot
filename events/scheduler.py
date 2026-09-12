import asyncio
import logging
import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Awaitable, Callable

import discord
from discord.ext import tasks

from db.guild_channels import get_last_channel, get_main_channel

# 한국시간(KST) 고정 오프셋. 서머타임이 없어서 UTC+9 고정으로 충분하다.
KST = timezone(timedelta(hours=9))

SLEEP_START = time(0, 0)
WAKE_TIME = time(6, 30)

# 취침 중 맨션 깨움 이벤트(방해금지 모드)가 발동한 밤은 "누가 중간에 깨워서 30분 더
# 잤다"는 컨셉으로 그날의 실제 기상 시각을 미룬다.
DELAYED_WAKE_TIME = time(7, 0)

# 쳇바퀴 에너지 드링크(암시장 포션, §24, 2026-09-12 신규)를 먹이면 그날 밤 취침
# 시각이 30분 늦춰진다 — DELAYED_WAKE_TIME과 정확히 거울상인 메커니즘.
DELAYED_SLEEP_START = time(0, 30)

_DailyCallback = Callable[[], Awaitable[None]]

# 테스트 서버 전용 채널 고정 상태. awake/asleep 채널은 실제 시간과 무관하게 그 상태로
# 강제되고, sync 채널은 실제 시간(is_sleep_time)을 그대로 따른다 — 봇이 스스로 올리는
# 전역 메시지도 이 서버에서는 항상 sync 채널로만 간다. awake/asleep 채널은 자동 게시
# 대상이 아니라 누군가 먼저 말을 걸었을 때만 반응하는 순수 반응형 채널이다.
TEST_GUILD_ID = 1541345080680644651
TEST_AWAKE_CHANNEL_ID = 1541345084493144068
TEST_ASLEEP_CHANNEL_ID = 1542920701214724186
TEST_SYNC_CHANNEL_ID = 1542920725185429677

# 오늘(KST) 밤 방해금지 이벤트가 발동했는지 — 발동한 그 날짜만 기록해두고, 다음 날이 되면
# 날짜가 안 맞아 자연히 무효화된다(별도 리셋 로직 불필요).
_late_wake_date: date | None = None

# 쳇바퀴 에너지 드링크로 그날 밤 취침이 지연됐는지 — mark_late_wake와 달리 "지연되는
# 그 자정의 날짜"(먹인 날+1일)를 저장한다. 00:00~00:30 사이의 판정 시점엔
# current_dt.date()가 이미 다음 날이기 때문(먹인 시점에 이미 +1일로 계산해서 넘겨야
# 함 — command/eat.py::_handle_potion 참고). 재시작 대비 복원은
# db/sleep_delay.py::get_pending_sleep_delay()가 담당.
_late_sleep_date: date | None = None


def mark_late_wake() -> None:
    """취침 중 맨션 깨움 이벤트(방해금지 모드)가 발동했을 때 호출한다 — 오늘(KST)의
    기상 시각을 06:30 대신 07:00로 늦춘다."""
    global _late_wake_date
    _late_wake_date = datetime.now(timezone.utc).astimezone(KST).date()


def is_late_wake_today() -> bool:
    """오늘(KST) 밤 방해금지 이벤트가 발동해서 기상이 07:00로 늦춰진 상태인지."""
    return _late_wake_date == datetime.now(timezone.utc).astimezone(KST).date()


def mark_late_sleep(for_date: date | None = None) -> None:
    """쳇바퀴 에너지 드링크를 먹였을 때 호출한다 — for_date(지연되는 그 자정의
    날짜, 생략 시 "내일")의 취침 시작 시각을 00:00 대신 00:30으로 늦춘다."""
    global _late_sleep_date
    _late_sleep_date = for_date or (datetime.now(timezone.utc).astimezone(KST).date() + timedelta(days=1))


def is_late_sleep_today() -> bool:
    """오늘(KST) 취침 시작이 쳇바퀴 에너지 드링크로 00:30으로 늦춰진 상태인지."""
    return _late_sleep_date == datetime.now(timezone.utc).astimezone(KST).date()


def is_sleep_time(now: datetime | None = None) -> bool:
    """지금이 한국시간 취침 시간대(00:00~06:30, 방해금지 발동 시 00:00~07:00, 쳇바퀴
    에너지 드링크로 지연 시 00:30~06:30/07:00)인지 — 실제 시간 기준, 관리자
    오버라이드 미반영."""
    current_dt = (now or datetime.now(timezone.utc)).astimezone(KST)
    sleep_start_boundary = DELAYED_SLEEP_START if _late_sleep_date == current_dt.date() else SLEEP_START
    wake_boundary = DELAYED_WAKE_TIME if _late_wake_date == current_dt.date() else WAKE_TIME
    return sleep_start_boundary <= current_dt.time() < wake_boundary


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


_MORNING_GREETING_WINDOW = timedelta(minutes=30)


def is_within_morning_greeting_window(now: datetime | None = None) -> bool:
    """오늘 기상 시각(정상 06:30, 지연 기상이면 07:00) 기준 +30분 이내인지 — 아침 인사
    자연어 보상(3-6) 판정용. is_sleep_time과 동일한 방식으로 오늘의 기상 시각을 정한다."""
    current_dt = (now or datetime.now(timezone.utc)).astimezone(KST)
    wake_boundary = DELAYED_WAKE_TIME if _late_wake_date == current_dt.date() else WAKE_TIME
    window_start = datetime.combine(current_dt.date(), wake_boundary, tzinfo=KST)
    window_end = window_start + _MORNING_GREETING_WINDOW
    return window_start <= current_dt < window_end


def resolve_broadcast_channel_id(guild_id: int, last_channel_id: int | None) -> int | None:
    """헬프 미 이벤트/아침 인사/취침 이벤트/공지처럼 봇이 스스로 올리는 전역 메시지가 어느
    채널로 갈지 결정한다. 테스트 서버는 항상 고정된 sync 채널로만 보낸다(자체 3채널 테스트
    체계가 이미 있어 새 지정 채널 시스템과 별개로 항상 우선) — awake/asleep 채널은
    반응형(누군가 먼저 말을 걸었을 때만 동작)이라 자동 게시 대상에서 제외된다. 그 외
    서버는 메인 채널로만 보낸다(서브 채널은 명령어 전용이라 방송 대상이 아니다) — 메인이
    아직 없으면 기존처럼 "마지막 호출된 채널"을 쓴다."""
    if guild_id == TEST_GUILD_ID:
        return TEST_SYNC_CHANNEL_ID
    main = get_main_channel(guild_id)
    if main is not None:
        return main
    return last_channel_id


async def broadcast_to_guilds(
    client: discord.Client,
    allowed_guild_ids,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    origin_guild_id: int | None = None,
) -> None:
    """모든 허용 서버의 방송 채널(resolve_broadcast_channel_id)에 같은 내용을 보낸다 —
    헬프 미/취침/기상/레벨업/업적/관리자 공지 등 전 서버 방송이 전부 공유하는 공용
    헬퍼(舊에는 관리자 콘솔 공지 전용이었으나 이후 여러 곳이 재사용하게 됨).

    2026-09-11 사용자 지시로 두 가지를 바꿨다:
    1. **`origin_guild_id`가 주어지면 그 서버에 먼저 보낸다** — 레벨업/업적 달성처럼
       특정 서버에서 벌어진 행동이 방송을 유발한 경우, 정작 그 행동을 한 사람이
       속한 서버가 "허용 서버 순회 순서상 마지막"이라 자기 서버 차례가 올 때까지
       기다려야 하는 체감 지연이 있었다 — 원인이 된 서버를 먼저 보내 즉시 확인할
       수 있게 한다.
    2. **나머지 서버는 순차 `await`가 아니라 `asyncio.gather`로 동시에 보낸다** —
       舊 방식은 서버 수만큼 매번 `await channel.send(...)`를 순서대로 기다려서,
       서버가 늘어날수록 전체 방송이 끝나는 데 걸리는 시간이 선형으로 늘어났다.
       기원 서버가 없는 방송(아침 인사/취침 전 언급/디저트 타임처럼 특정 서버가
       아니라 그날 전역에서 벌어지는 이벤트)도 이 동시 전송만으로 자동으로
       빨라진다."""
    kwargs: dict = {}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed

    async def _send_to(guild: discord.Guild) -> None:
        channel_id = resolve_broadcast_channel_id(guild.id, await get_last_channel(guild.id))
        if channel_id is None:
            return
        channel = guild.get_channel(channel_id)
        if channel is None:
            return
        try:
            await channel.send(**kwargs)
        except discord.HTTPException:
            logging.exception("Failed to broadcast message in guild %s", guild.id)

    origin_guild: discord.Guild | None = None
    rest: list[discord.Guild] = []
    for guild in client.guilds:
        if guild.id not in allowed_guild_ids:
            continue
        if origin_guild_id is not None and guild.id == origin_guild_id:
            origin_guild = guild
        else:
            rest.append(guild)

    if origin_guild is not None:
        await _send_to(origin_guild)
    if rest:
        await asyncio.gather(*(_send_to(guild) for guild in rest))


def format_footer_time(now: datetime) -> str:
    """시스템 임베드 footer 공용 포맷. "GMT"/"KST" 같은 시간대 약어는 쓰지 않는다."""
    period = "AM" if now.hour < 12 else "PM"
    return f"{now.strftime('%Y. %m. %d.')} {now.strftime('%I:%M')} {period}"


def _guarded(callback: _DailyCallback) -> _DailyCallback:
    """`discord.ext.tasks.Loop`는 콜백에서 처리되지 않은 예외가 새면(설령 `.error()`
    핸들러를 따로 달아도) 그 반복 자체를 영구히 멈춘다 — 프로세스가 재시작되기 전까지는
    다시 안 돈다(2026-09-11 발견 — 이 안전장치가 없어서 취침 전 최다 대화자 방송(§3-5)이
    하루 통째로 누락된 사고가 있었다). 그날그날의 우연한 예외 하나가 그 태스크를
    영구히 죽여버리는 걸 막기 위해, `start_daily`/`start_interval`이 등록하는 모든
    콜백을 여기서 한 번 감싸 예외를 로그만 남기고 삼킨다 — 다음 주기에 다시 정상
    실행된다."""

    async def _wrapped() -> None:
        try:
            await callback()
        except Exception:
            logging.exception(
                "Unhandled exception in scheduled task %r — this occurrence was skipped, "
                "next scheduled run is unaffected",
                getattr(callback, "__name__", callback),
            )

    return _wrapped


def start_daily(hour: int, minute: int, callback: _DailyCallback) -> tasks.Loop:
    """매일 한국시간 hour:minute에 callback을 한 번 실행하는 백그라운드 태스크를 시작한다.

    헬프 미 이벤트/아침 인사(하루 06:30 기상 시각에 실행)와 취침 이벤트(00:00) 둘 다
    이 함수 위에서 등록한다. `_guarded`로 감싸므로 callback 내부에서 예외가 나도
    이 일일 반복 자체는 계속 살아있다.
    """
    kst_time = time(hour=hour, minute=minute, tzinfo=KST)
    loop = tasks.loop(time=kst_time)(_guarded(callback))
    loop.start()
    return loop


def start_interval(seconds: float, callback: _DailyCallback) -> tasks.Loop:
    """seconds 간격으로 callback을 반복 실행하는 백그라운드 태스크를 시작한다.

    헬프 미 이벤트의 "예정 시각이 됐는지" / "만료됐는데 무응답인지" 주기 점검에 쓴다.
    `_guarded`로 감싸므로 callback 내부에서 예외가 나도 이 반복 자체는 계속 살아있다.
    """
    loop = tasks.loop(seconds=seconds)(_guarded(callback))
    loop.start()
    return loop


def random_times_in_window(
    count: int, start: time, end: time, min_gap_minutes: int = 0
) -> list[time]:
    """[start, end) 구간 안에서 서로 다른 count개의 시각을 무작위로 뽑아 오름차순으로 반환한다.

    min_gap_minutes > 0이면 인접한 두 시각 사이 간격이 항상 그 값 이상이 되도록 보장한다.
    재시도(rejection sampling) 없이, 구간을 (count-1)*min_gap만큼 줄인 뒤 뽑아서 각
    포인트에 순서대로 간격을 더하는 방식으로 원천적으로 보장한다.
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
