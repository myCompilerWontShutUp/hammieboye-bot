from datetime import datetime, time, timedelta

import discord

from core.base import EMBED_COLOR
from db.call_events import get_scheduled_between
from db.daily_stats import ensure_daily_stats, update_daily_stats
from db.snacks import consume_snack, get_inventory
from events.dessert_time import SLOTS, slot_end
from events.scheduler import KST, DELAYED_WAKE_TIME, WAKE_TIME, format_footer_time, is_late_wake_today

ITEM_ID = "hammie_schedule"

_NO_ITEM_MESSAGE = "어라, 햄미 일정표를 안 가지고 있는데?? `/암시장`에서 먼저 사 와줄래?? _(갸웃)_"

_SLOT_LABELS: dict[str, str] = {"morning": "아침", "noon": "점심", "evening": "저녁"}

_RECOMMENDATION = (
    "암시장은 저녁 시간대(밤)에만 열리니까, 사자마자 바로 써서 그날 일과를 전부 "
    "확인해보는 걸 추천해!!"
)


def _fmt(t: time) -> str:
    return t.strftime("%H:%M")


async def _build_embed() -> discord.Embed:
    today_kst = datetime.now(KST).date()
    start_of_day = datetime.combine(today_kst, time(0, 0), tzinfo=KST)
    end_of_day = start_of_day + timedelta(days=1)

    wake_time = DELAYED_WAKE_TIME if is_late_wake_today() else WAKE_TIME
    timed_entries: list[tuple[time, str]] = []

    for slot_name, slot_start in SLOTS.items():
        label = _SLOT_LABELS.get(slot_name, slot_name)
        timed_entries.append(
            (slot_start, f"🍪 디저트 타임({label}): {_fmt(slot_start)} ~ {_fmt(slot_end(slot_start))}")
        )

    events = await get_scheduled_between(start_of_day, end_of_day)
    for event in events:
        scheduled_at = datetime.fromisoformat(event["scheduled_at"]).astimezone(KST)
        timed_entries.append((scheduled_at.time(), f"🆘 헬프 미 이벤트: {_fmt(scheduled_at.time())}"))

    timed_entries.sort(key=lambda entry: entry[0])

    lines = [f"🌅 기상: {_fmt(wake_time)}"]
    lines += [text for _, text in timed_entries]
    lines.append("🌙 취침: 00:00 (자정)")

    embed = discord.Embed(
        title="📅 오늘의 햄미 일정표",
        description="\n".join(lines) + f"\n\n{_RECOMMENDATION}",
        color=EMBED_COLOR,
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed


async def handle_use(user_id: int) -> discord.Embed | str:
    """`/사용 햄미 일정표` — 오늘 하루치 일정을 보여준다(본인에게만 보임). "사용한
    날을 기준으로 작동"하므로 오늘 첫 사용에만 실제로 1개를 소비하고, 같은 날
    재사용은 소비 없이 몇 번이든 다시 보여준다(daily_stats.schedule_activated_today,
    2026-09-08 신규 — 매일 새 행이라 별도 만료 처리 없이 자정에 자연히 초기화된다).
    쓴 다음 날 이 인스턴스가 "소멸"한다는 요청은, 첫 사용 시점에 이미 재고를 1개
    소비해버리는 것으로 충족된다 — 다음 날 다시 보려면 새로 산 개체가 필요하다."""
    stats = await ensure_daily_stats(user_id)
    if stats.get("schedule_activated_today"):
        return await _build_embed()

    inventory = await get_inventory(user_id)
    owned = any(row["snack_id"] == ITEM_ID and row["quantity"] > 0 for row in inventory)
    if not owned:
        return _NO_ITEM_MESSAGE

    if not await consume_snack(user_id, ITEM_ID):
        return _NO_ITEM_MESSAGE

    await update_daily_stats(user_id, {"schedule_activated_today": True})
    return await _build_embed()
