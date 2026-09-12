import asyncio
import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Awaitable, Callable

import discord

from command.black_market_catalog import BY_ID as _BLACK_MARKET_BY_ID
from command.vending_catalog import BY_ID as _VENDING_BY_ID
from config import ALLOWED_GUILD_IDS
from core.base import SYSTEM_EMBED_COLOR
from core.discord_names import resolve_real_name
from db.dessert_slot_kind import claim_slot_kind
from db.daily_stats import get_dessert_feeders_for, kst_today_str
from db.users import get_created_at_map
from events import presence
from events.scheduler import KST, broadcast_to_guilds, format_footer_time

# 디저트 타임(간식)/드링킹 타임(음료·포션) 두 카탈로그를 합쳐서 조회 — 랭킹이 어느
# 타임에서 계산되든 두 출처의 품목 가격을 모두 알 수 있어야 한다(2026-09-12, §24).
BY_ID = {**_VENDING_BY_ID, **_BLACK_MARKET_BY_ID}

_client: discord.Client | None = None

# 하루 3번, 각 30분짜리 디저트 타임 — /사용(간식)은 이 윈도우 안에서만, 슬롯당 1번만
# 통한다(daily_stats.dessert_fed_today가 실제 "이미 먹였는지" 판정, 이 모듈은 순수
# 시간 계산만).
SLOTS: dict[str, time] = {
    "morning": time(8, 0),
    "noon": time(12, 30),
    "evening": time(17, 0),
}
WINDOW = timedelta(minutes=30)

_OPEN_LINES = (
    "배가 출출해!! 간식 먹여줄래?? _(기대)_",
    "냠냠 하고 싶은 기분이야!! `/사용`으로 간식 줘봐!! _(설렘)_",
    "간식 생각이 간절해!! _(칭얼)_",
    "출출한데 간식 좀 줄래?? _(두근)_",
    "간식 먹고 싶어!! 뭐 줄 거야?? _(기대)_",
    "배 속에서 꼬르륵 소리가 나!! _(웃음)_",
    "지금 간식 주면 완전 좋아!! _(애교)_",
    "출출해서 간식이 당겨!! _(칭얼)_",
    "간식 좀 챙겨줄래?? 배고파!! _(칭얼)_",
    "지금 딱 간식 먹고 싶은 타이밍이야!! _(설렘)_",
    "누가 간식 좀 안 주나?? _(두리번)_",
    "간식 먹을 준비 완료!! _(신남)_",
    "배가 살살 아파올 정도로 출출해!! _(엄살)_",
    "지금 간식 주면 진짜 조아!! _(애교)_",
    "간식이 먹고 싶어져써!! _(기대)_",
    "출출함이 몰려와!! 간식 좀 줄래?? _(칭얼)_",
    "냠냠 하고 싶다!! 간식 가져다줘!! _(설렘)_",
    "배고픈 신호가 왔어!! _(웃음)_",
    "간식 시간인 것 같아!! _(궁금)_",
    "지금 간식 주면 최고로 행복할 것 같아!! _(들뜸)_",
)
_CLOSE_LINES = (
    "냠냠 잘 먹었다!! _(만족)_",
    "배부르다!! 잘 먹었어!! _(뿌듯)_",
    "간식 다 먹었어!! 잘 먹었습니다!! _(방긋)_",
    "냠냠, 오늘 간식도 맛있었어!! _(행복)_",
    "잘 먹었어!! 다음에 또 줘!! _(웃음)_",
    "배가 빵빵해!! 잘 먹었다!! _(만족)_",
    "간식 시간 끝!! 고마워!! _(감사)_",
    "냠냠 끝!! 다음에 또 줘!! _(기대)_",
    "잘 먹었습니다!! 행복해!! _(뿌듯)_",
    "간식 다 먹어서 배불러!! _(방긋)_",
    "냠냠, 배부르게 잘 먹었어!! _(만족)_",
    "오늘 간식도 최고였어!! 잘 먹었어!! _(행복)_",
    "잘 먹었어!! 고마워!! _(감사)_",
    "잘 먹었다!! 다음에 또 부탁해!! _(웃음)_",
    "냠냠, 이번 간식도 만족!! _(뿌듯)_",
    "배부르게 잘 먹었어!! _(만족)_",
    "잘 먹었습니다!! 다음을 기다릴게!! _(기대)_",
    "간식 냠냠 행복해!! _(행복)_",
    "잘 먹었어!! 다음에 또 만나자!! _(방긋)_",
    "냠냠 끝, 잘 먹었다구!! _(만족)_",
)

# "드링킹 타임"(§24, 2026-09-12 신규) 전용 문구 풀 — 디저트 타임과 동일한 3슬롯을
# 공유하지만 그날 그 슬롯이 50% 확률로 이쪽으로 결정되면 이 문구/임베드를 쓴다
# (get_or_roll_slot_kind 참고). "디저트 타임"이라는 이름 자체를 안 쓰는 것과 동일한
# 원칙으로 "드링킹 타임"이라는 이름도 햄미의 실제 발화(_DRINK_OPEN_LINES/
# _DRINK_CLOSE_LINES)에서는 쓰지 않는다 — 목마름/음료 감각 표현만 쓴다.
_DRINK_OPEN_LINES = (
    "목이 좀 마른 것 같아!! _(기대)_",
    "시원한 게 마시고 싶어!! `/사용`으로 줘봐!! _(설렘)_",
    "음료 생각이 간절해!! _(칭얼)_",
    "목마른데 뭐 좀 마실 수 있을까?? _(두근)_",
    "시원한 거 마시고 싶어!! 뭐 줄 거야?? _(기대)_",
    "목이 칼칼해!! _(웃음)_",
    "지금 음료 주면 완전 좋아!! _(애교)_",
    "목말라서 뭔가 당겨!! _(칭얼)_",
    "음료 좀 챙겨줄래?? 목말라!! _(칭얼)_",
    "지금 딱 뭔가 마시고 싶은 타이밍이야!! _(설렘)_",
    "누가 시원한 거 좀 안 주나?? _(두리번)_",
    "마실 준비 완료!! _(신남)_",
    "목이 칼칼할 정도로 마르다!! _(엄살)_",
    "지금 마시면 진짜 조아!! _(애교)_",
    "뭔가 마시고 싶어져써!! _(기대)_",
    "목마름이 몰려와!! 뭐 좀 줄래?? _(칭얼)_",
    "꿀꺽 하고 싶다!! 음료 가져다줘!! _(설렘)_",
    "목마른 신호가 왔어!! _(웃음)_",
    "마실 시간인 것 같아!! _(궁금)_",
    "지금 시원한 거 주면 최고로 행복할 것 같아!! _(들뜸)_",
)
_DRINK_CLOSE_LINES = (
    "꿀꺽 잘 마셨다!! _(만족)_",
    "속이 시원해!! 잘 마셨어!! _(뿌듯)_",
    "다 마셨어!! 잘 마셨습니다!! _(방긋)_",
    "꿀꺽, 오늘 것도 맛있었어!! _(행복)_",
    "잘 마셨어!! 다음에 또 줘!! _(웃음)_",
    "속이 시원해졌어!! 잘 마셨다!! _(만족)_",
    "마시는 시간 끝!! 고마워!! _(감사)_",
    "꿀꺽 끝!! 다음에 또 줘!! _(기대)_",
    "잘 마셨습니다!! 행복해!! _(뿌듯)_",
    "다 마셔서 속이 시원해!! _(방긋)_",
    "꿀꺽, 시원하게 잘 마셨어!! _(만족)_",
    "오늘 것도 최고였어!! 잘 마셨어!! _(행복)_",
    "잘 마셨어!! 고마워!! _(감사)_",
    "잘 마셨다!! 다음에 또 부탁해!! _(웃음)_",
    "꿀꺽, 이번 것도 만족!! _(뿌듯)_",
    "시원하게 잘 마셨어!! _(만족)_",
    "잘 마셨습니다!! 다음을 기다릴게!! _(기대)_",
    "꿀꺽 행복해!! _(행복)_",
    "잘 마셨어!! 다음에 또 만나자!! _(방긋)_",
    "꿀꺽 끝, 잘 마셨다구!! _(만족)_",
)

# 시스템 안내 임베드(2026-09-05 신규) — 사람들이 헬프 미 이벤트와 헷갈려한다는
# 피드백으로, 방송 밑에 항상 이 고정 임베드를 붙여 참여 방법을 명확히 한다. 햄미의
# 말투(반말/오타)는 위 문구에만 쓰고, 이 안내는 시스템 라벨이라 정중체로 고정한다.
_ANNOUNCE_TITLE = "🍪 햄미의 디저트 타임"
_ANNOUNCE_DESCRIPTION = "`/사용`을 이용해 햄미에게 음식을 가져다 주세요! 음식은 자판기에서 구매 가능합니다."

# "드링킹 타임" 전용 안내 임베드(2026-09-12 신규) — 위와 동일한 원칙, 자판기(음료)/
# 암시장(포션) 둘 다 이 시간에만 급여 가능하다는 걸 안내한다.
_DRINK_ANNOUNCE_TITLE = "🥤 햄미의 드링킹 타임"
_DRINK_ANNOUNCE_DESCRIPTION = (
    "`/사용`을 이용해 햄미에게 마실 것을 가져다 주세요! 음료는 자판기에서, "
    "포션은 암시장에서 구매 가능합니다."
)


def _build_announce_embed() -> discord.Embed:
    embed = discord.Embed(title=_ANNOUNCE_TITLE, description=_ANNOUNCE_DESCRIPTION, color=SYSTEM_EMBED_COLOR)
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed


def _build_drink_announce_embed() -> discord.Embed:
    embed = discord.Embed(
        title=_DRINK_ANNOUNCE_TITLE, description=_DRINK_ANNOUNCE_DESCRIPTION, color=SYSTEM_EMBED_COLOR
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed


# 디저트/드링킹 타임 종료 방송에 붙는 "이번 타임 최고 후원자" 랭킹 — 상위 몇 명까지
# 보여줄지. 제목은 그 슬롯이 어느 kind였는지에 따라 달라진다(2026-09-12).
_LEADERBOARD_SIZE = 5
_LEADERBOARD_TITLE_BY_KIND = {
    "dessert": "🏆 이번 디저트 타임 최고 후원자!!",
    "drink": "🏆 이번 드링킹 타임 최고 후원자!!",
}
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


async def _build_leaderboard_text(slot: str, kind: str) -> str | None:
    """그 슬롯에서 오늘 아무도 먹이지 않았으면 None(방송문 자체에 랭킹 섹션을 안
    붙인다). 우선순위: (1) 비싼 품목일수록 (2) 같은 값이면 먼저 먹인 사람 (3) 그마저
    같으면 가입일이 빠른 사람(/랭킹(호감도 카테고리)의 동점 타이브레이크와 동일한
    원칙). kind는 제목 문구 선택용(디저트/드링킹 타임 구분, 2026-09-12)."""
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
    return _LEADERBOARD_TITLE_BY_KIND[kind] + "\n" + "\n".join(lines)


def slot_end(start: time) -> time:
    return (datetime.combine(date(2000, 1, 1), start) + WINDOW).time()


def current_slot(now: datetime | None = None) -> str | None:
    """지금이 어느 디저트/드링킹 타임 슬롯(30분 윈도우) 안인지 — 아니면 None. /사용의
    시간대 유효성 검사는 이 함수 하나로 충분하다(슬롯당 1회 제한은 daily_stats로
    별도 판정, 그 슬롯이 간식/음료 중 무엇인지는 get_or_roll_slot_kind가 별도 판정)."""
    current_dt = (now or datetime.now(timezone.utc)).astimezone(KST)
    for name, start in SLOTS.items():
        window_start = datetime.combine(current_dt.date(), start, tzinfo=KST)
        window_end = window_start + WINDOW
        if window_start <= current_dt < window_end:
            return name
    return None


async def get_or_roll_slot_kind(slot: str) -> str:
    """오늘 이 슬롯이 "dessert"(간식)인지 "drink"(음료)인지 50/50으로 굴려서 확정하고,
    이미 확정돼 있으면(다른 호출이 먼저 확정했으면) 그 값을 그대로 돌려준다 —
    claim_dessert_slot_kind RPC가 (날짜,슬롯) 단위로 "먼저 도착한 쪽이 이긴다" 방식으로
    멱등하게 처리하므로, 오픈 방송과 /사용 급여 시점이 어느 순서로 이 함수를 호출해도
    항상 하나의 결과로 수렴한다(2026-09-12, §24)."""
    kind = random.choice(("dessert", "drink"))
    return await claim_slot_kind(kst_today_str(), slot, kind)


async def broadcast_open() -> None:
    if _client is None:
        return
    # start_daily가 3개 슬롯 전부 동일한 콜백(이 함수)을 인자 없이 등록하므로,
    # "지금 몇 시인지"로 스스로 어느 슬롯이 열렸는지 알아낸다 — cron이 정확히 슬롯
    # 시작 시각에 발동하므로 이 순간 current_slot()은 신뢰성 있게 방금 연 슬롯
    # 이름을 반환한다(2026-09-12).
    slot = current_slot()
    if slot is None:
        return
    kind = await get_or_roll_slot_kind(slot)
    await presence.enter_snack_request()
    if kind == "dessert":
        content, embed = random.choice(_OPEN_LINES), _build_announce_embed()
    else:
        content, embed = random.choice(_DRINK_OPEN_LINES), _build_drink_announce_embed()
    await broadcast_to_guilds(_client, ALLOWED_GUILD_IDS, content=content, embed=embed)


async def broadcast_close(slot: str) -> None:
    if _client is None:
        return
    kind = await get_or_roll_slot_kind(slot)  # 이미 확정된 값을 그대로 재조회(멱등)
    await presence.wake_up()
    text = random.choice(_CLOSE_LINES if kind == "dessert" else _DRINK_CLOSE_LINES)
    leaderboard = await _build_leaderboard_text(slot, kind)
    if leaderboard is not None:
        text += f"\n\n{leaderboard}"
    await broadcast_to_guilds(_client, ALLOWED_GUILD_IDS, content=text)
