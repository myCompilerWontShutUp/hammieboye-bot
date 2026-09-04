import random
from datetime import date, datetime, time, timedelta, timezone

import discord

from config import ALLOWED_GUILD_IDS
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


def init(client: discord.Client) -> None:
    global _client
    _client = client


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


async def broadcast_close() -> None:
    if _client is None:
        return
    await broadcast_to_guilds(_client, ALLOWED_GUILD_IDS, content=random.choice(_CLOSE_LINES))
