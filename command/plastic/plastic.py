import random
from datetime import datetime, timedelta, timezone

from db.affection import add_affection, format_affection_notice
from db.daily_stats import ensure_daily_stats, update_daily_stats
from db.users import get_user, set_plastic_cooldown

# CLAUDE.md 섹션 3-1
_COOLDOWN = timedelta(minutes=10)
_METHOD = "plastic_bottle"
_STREAK_TARGET = 3
_SUCCESS_RATE = 0.5

# 섹션 4-5: 쿨타임 고정 메시지를 이 횟수까지는 그냥 보여주고, 그다음부터 남용 페널티
_COOLDOWN_ABUSE_FREE_COUNT = 3

_COOLDOWN_MESSAGE = "아직 쿨타임이야!! 쪼금만 기다려줘 뾱"
_FAIL_MESSAGE = "어이쿠 놓쳤다!! 다음엔 꼭 잡을게 뾱뾱"
_SUCCESS_MESSAGE = "잡았다!! 신나는 춤 쟈쟈쟉!! 뾱뾱"
_SUCCESS_STREAK_MESSAGE = "3번 연속 성공!!! 완전 신난다 쟈쟈쟉쟈쟈쟉!! 뾱뾱뾱"


async def handle(user_id: int) -> str:
    user = await get_user(user_id)
    now = datetime.now(timezone.utc)

    cooldown_until = user.get("plastic_cooldown_until")
    if cooldown_until is not None and datetime.fromisoformat(cooldown_until) > now:
        delta, current = await _register_cooldown_abuse(user_id)
        return _with_notice(_COOLDOWN_MESSAGE, delta, current)

    stats = await ensure_daily_stats(user_id)
    total_delta = 0
    current_affection = user["affection"]

    if random.random() >= _SUCCESS_RATE:
        await set_plastic_cooldown(user_id, now + _COOLDOWN)
        await update_daily_stats(user_id, {"plastic_streak": 0})
        return _FAIL_MESSAGE

    new_streak = stats["plastic_streak"] + 1
    update_fields = {"plastic_streak": new_streak}
    message = _SUCCESS_MESSAGE

    if not stats["plastic_success_claimed"]:
        result = await add_affection(user_id, 1, _METHOD)
        total_delta += result["applied_amount"]
        current_affection = result["new_affection"]
        update_fields["plastic_success_claimed"] = True

    if new_streak >= _STREAK_TARGET and not stats["plastic_streak_bonus_claimed"]:
        result = await add_affection(user_id, 3, _METHOD)
        total_delta += result["applied_amount"]
        current_affection = result["new_affection"]
        update_fields["plastic_streak_bonus_claimed"] = True
        message = _SUCCESS_STREAK_MESSAGE

    await update_daily_stats(user_id, update_fields)
    return _with_notice(message, total_delta, current_affection)


async def _register_cooldown_abuse(user_id: int) -> tuple[int, int]:
    stats = await ensure_daily_stats(user_id)
    counts = dict(stats.get("cooldown_abuse_counts") or {})
    count = counts.get(_METHOD, 0) + 1
    counts[_METHOD] = count
    await update_daily_stats(user_id, {"cooldown_abuse_counts": counts})

    if count > _COOLDOWN_ABUSE_FREE_COUNT:
        result = await add_affection(user_id, -1)
        return result["applied_amount"], result["new_affection"]

    user = await get_user(user_id)
    return 0, user["affection"]


def _with_notice(message: str, delta: int, current: int) -> str:
    if delta == 0:
        return message
    return message + format_affection_notice(delta, current)
