import asyncio
import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Awaitable, Callable

import discord

from command.vending_catalog import BY_ID
from config import ALLOWED_GUILD_IDS
from core.discord_names import resolve_real_name
from db.daily_stats import get_dessert_feeders_for, kst_today_str
from db.users import get_created_at_map
from events.scheduler import KST, broadcast_to_guilds

_client: discord.Client | None = None

# 하루 3번, 각 30분짜리 디저트 타임 — /먹어는 이 윈도우 안에서만, 슬롯당 1번만 통한다
# (daily_stats.dessert_fed_today가 실제 "이미 먹였는지" 판정, 이 모듈은 순수 시간 계산만).
SLOTS: dict[str, time] = {
    "morning": time(8, 0),
    "noon": time(12, 30),
    "evening": time(17, 0),
}
WINDOW = timedelta(minutes=30)

_OPEN_LINES = (
    "디저트 타임이야!! 간식 먹여줄래?? _(신남)_",
    "냠냠 시간이 왔어!! /먹어로 간식 줘봐!! _(설렘)_",
    "배가 출출해!! 지금이 디저트 타임이야!! _(기대)_",
    "간식 먹을 시간!! 뭐 줄 거야?? _(두근)_",
    "디저트 타임 개시!! _(반짝)_",
    "지금이야!! 간식 먹여줄 시간!! _(신남)_",
    "냠냠 타임 시작!! _(방긋)_",
    "디저트 타임!! 뭐 먹을지 기대돼!! _(설렘)_",
    "간식 시간이 왔어!! _(들뜸)_",
    "지금 간식 주면 완전 좋아!! _(애교)_",
    "디저트 타임이야!! 배고파!! _(칭얼)_",
    "냠냠 시간!! /먹어 기다리고 있을게!! _(기대)_",
    "간식 먹을 준비 완료!! _(신남)_",
    "디저트 타임 왔다!! _(반짝)_",
    "지금 먹여주면 진짜 조아!! _(애교)_",
    "간식 시간이야!! 뭐가 좋을까?? _(궁금)_",
    "냠냠, 디저트 타임 시작됐어!! _(방긋)_",
    "지금이 간식 찬스야!! _(신남)_",
    "디저트 타임!! 배 속에서 신호 왔어!! _(웃음)_",
    "간식 먹을 시간이 됐어!! _(설렘)_",
)
_CLOSE_LINES = (
    "냠냠 잘 먹었다!! 디저트 타임 끝!! _(만족)_",
    "배부르다!! 잘 먹었어!! _(뿌듯)_",
    "디저트 타임 종료!! 잘 먹었습니다!! _(방긋)_",
    "냠냠, 오늘 간식도 맛있었어!! _(행복)_",
    "잘 먹었어!! 다음 디저트 타임에 또 만나!! _(웃음)_",
    "배가 빵빵해!! 잘 먹었다!! _(만족)_",
    "디저트 타임 끝!! 고마워!! _(감사)_",
    "냠냠 끝!! 다음 시간에 또 줘!! _(기대)_",
    "잘 먹었습니다!! 행복해!! _(뿌듯)_",
    "디저트 타임 마감!! 잘 먹었어!! _(방긋)_",
    "냠냠, 배부르게 잘 먹었어!! _(만족)_",
    "오늘 간식도 최고였어!! 잘 먹었어!! _(행복)_",
    "디저트 타임 종료됐어!! 고마워!! _(감사)_",
    "잘 먹었다!! 다음에 또 부탁해!! _(웃음)_",
    "냠냠, 이번 디저트 타임도 만족!! _(뿌듯)_",
    "배부르게 잘 먹었어!! 디저트 타임 끝!! _(만족)_",
    "잘 먹었습니다!! 다음 시간을 기다릴게!! _(기대)_",
    "디저트 타임 끝!! 냠냠 행복해!! _(행복)_",
    "잘 먹었어!! 다음에 또 만나자!! _(방긋)_",
    "냠냠 끝, 잘 먹었다구!! _(만족)_",
)


# 디저트 타임 종료 방송에 붙는 "이번 타임 최고 후원자" 랭킹 — 상위 몇 명까지 보여줄지.
_LEADERBOARD_SIZE = 5
_LEADERBOARD_TITLE = "🏆 이번 디저트 타임 최고 후원자!!"
# fed_at이 없는(마이그레이션 이전) 데이터는 항상 맨 뒤로 밀리게 아주 먼 미래 값을 쓴다.
_NO_TIMESTAMP_FALLBACK = "9999-12-31T23:59:59+00:00"


def init(client: discord.Client) -> None:
    global _client
    _client = client


def close_callback_for(slot: str) -> Callable[[], Awaitable[None]]:
    """start_daily(events/scheduler.py)는 인자 없는 콜백만 받으므로, 슬롯 이름을 클로저로
    가둔 래퍼를 슬롯마다 하나씩 만들어준다(functools.partial은 discord.py의
    inspect.iscoroutinefunction 검사를 못 통과해서 못 씀)."""

    async def _closer() -> None:
        await broadcast_close(slot)

    return _closer


async def _build_leaderboard_text(slot: str) -> str | None:
    """그 슬롯에서 오늘 간식을 먹인 사람이 하나도 없으면 None(방송문 자체에 랭킹 섹션을
    안 붙인다). 우선순위: (1) 비싼 간식일수록 (2) 같은 값이면 먼저 먹인 사람
    (3) 그마저 같으면 가입일이 빠른 사람(/랭킹의 동점 타이브레이크와 동일한 원칙)."""
    feeders = await get_dessert_feeders_for(kst_today_str(), slot)
    # 카탈로그에서 사라진 간식 id(있을 가능성은 낮지만) 등 가격을 모르는 항목은 랭킹
    # 자체에서 제외한다 — 번호가 중간에 비는 것보다 아예 안 보이는 게 낫다.
    feeders = [f for f in feeders if f["snack_id"] in BY_ID]
    if not feeders:
        return None

    created_at_map = await get_created_at_map([f["user_id"] for f in feeders])

    def sort_key(feeder: dict) -> tuple:
        price = BY_ID[feeder["snack_id"]].price
        fed_at = feeder["fed_at"] or _NO_TIMESTAMP_FALLBACK
        created_at = created_at_map.get(feeder["user_id"], _NO_TIMESTAMP_FALLBACK)
        return (-price, fed_at, created_at)

    feeders.sort(key=sort_key)
    top = feeders[:_LEADERBOARD_SIZE]

    names = await asyncio.gather(*(resolve_real_name(_client, f["user_id"]) for f in top))
    lines = [
        f"{i + 1}. {name} — {BY_ID[f['snack_id']].name}"
        for i, (name, f) in enumerate(zip(names, top))
    ]
    return _LEADERBOARD_TITLE + "\n" + "\n".join(lines)


def slot_end(start: time) -> time:
    return (datetime.combine(date(2000, 1, 1), start) + WINDOW).time()


def current_slot(now: datetime | None = None) -> str | None:
    """지금이 어느 디저트 타임 슬롯(30분 윈도우) 안인지 — 아니면 None. /먹어의 시간대
    유효성 검사는 이 함수 하나로 충분하다(슬롯당 1회 제한은 daily_stats로 별도 판정)."""
    current_dt = (now or datetime.now(timezone.utc)).astimezone(KST)
    for name, start in SLOTS.items():
        window_start = datetime.combine(current_dt.date(), start, tzinfo=KST)
        window_end = window_start + WINDOW
        if window_start <= current_dt < window_end:
            return name
    return None


async def broadcast_open() -> None:
    if _client is None:
        return
    await broadcast_to_guilds(_client, ALLOWED_GUILD_IDS, content=random.choice(_OPEN_LINES))


async def broadcast_close(slot: str) -> None:
    if _client is None:
        return
    text = random.choice(_CLOSE_LINES)
    leaderboard = await _build_leaderboard_text(slot)
    if leaderboard is not None:
        text += f"\n\n{leaderboard}"
    await broadcast_to_guilds(_client, ALLOWED_GUILD_IDS, content=text)
