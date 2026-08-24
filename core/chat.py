from datetime import datetime, timedelta, timezone

import discord

from command.base import normalize
from command.help.help import handle as help_handle
from command.info.info import handle as info_handle
from core import affection_guide, call_event, intent
from db.affection import add_affection, format_affection_notice
from db.daily_stats import ensure_daily_stats, update_daily_stats
from db.history import get_recent, log
from responses.engine import NEGATIVE_EMOTIONS, get_response

# CLAUDE.md 섹션 4-1: 분당 자연어 최대 10회, 초과분 1회당 -1
_RATE_LIMIT_PER_MINUTE = 10
_RATE_LIMIT_WINDOW = timedelta(seconds=60)

# CLAUDE.md 섹션 4-2: 히스토리(30분/최대 50개) 내 누적 3번 반복되면 그다음부터 -1
_HISTORY_WINDOW = timedelta(minutes=30)
_REPEAT_THRESHOLD = 3

# CLAUDE.md 섹션 2: 음수 호감도 구간표. 완전 무응답이 아니라 짧은 행동 텍스트로 반응한다.
_BITE_THRESHOLD = -20
_IGNORE_RESPONSE = "(무시)"
_BITE_RESPONSE = "(콱 깨묾)"

# CLAUDE.md 섹션 3-3 / 4-3
_HAPPY_EMOTION = "행복함"
_HAPPY_METHOD = "happy_emotion"
_NEGATIVE_EMOTION_STREAK_THRESHOLD = 2
_NEGATIVE_EMOTION_DAILY_THRESHOLD = 5


async def handle_natural_language(
    user_id: int, guild_id: int, text: str, affection: int
) -> str | discord.Embed | tuple[str, discord.Embed]:
    now = datetime.now(timezone.utc)
    recent = await get_recent(user_id, since=now - _HISTORY_WINDOW)

    total_delta = 0
    current_affection = affection

    def _record(result: dict) -> None:
        nonlocal total_delta, current_affection
        total_delta += result["applied_amount"]
        current_affection = result["new_affection"]

    # 4-1: 분당 자연어 과호출 — 슬라이딩 윈도우로 집계 (고정 버킷이면 경계에서 우회 가능, 섹션 9-1-4 참고)
    recent_in_last_minute = sum(
        1
        for row in recent
        if datetime.fromisoformat(row["created_at"]) >= now - _RATE_LIMIT_WINDOW
    )
    if recent_in_last_minute >= _RATE_LIMIT_PER_MINUTE:
        _record(await add_affection(user_id, -1))

    # 4-2: 동일 발화 반복 — 정규화 후 비교, 히스토리 내 누적 3번이면 그다음부터 페널티
    normalized_text = normalize(text)
    repeat_count = sum(1 for row in recent if normalize(row["content"]) == normalized_text)
    if repeat_count >= _REPEAT_THRESHOLD:
        _record(await add_affection(user_id, -1))

    await log(user_id, guild_id, text)

    # 3-2 부름 이벤트 응답 판정은 호감도가 음수여도 예외적으로 항상 시도한다 (섹션 2 예외 규정).
    event_delta = await call_event.handle_potential_response(user_id, guild_id, text)
    if event_delta:
        total_delta += event_delta
        current_affection += event_delta

    # 우선순위 2, 3: 자연어를 고정 명령어/프롬프트 캐시(자기소개 등)로 리다이렉트할 수 있는지 먼저 확인한다.
    routed = await _route_by_intent(user_id, text)
    if routed is not None:
        return _finalize(routed, total_delta, current_affection)

    # 우선순위 4: 어디에도 해당 안 되면 자연어 생성으로 넘어간다.
    if affection < 0:
        base = _BITE_RESPONSE if affection <= _BITE_THRESHOLD else _IGNORE_RESPONSE
        return _finalize(base, total_delta, current_affection)

    result = await get_response(text)
    if result.emotion is not None:
        emotion_delta = await _apply_emotion_effects(user_id, result.emotion)
        total_delta += emotion_delta
        if emotion_delta:
            current_affection += emotion_delta
    return _finalize(result.text, total_delta, current_affection)


async def _route_by_intent(
    user_id: int, text: str
) -> str | discord.Embed | tuple[str, discord.Embed] | None:
    label = await intent.classify(text)
    if label == "help":
        return await help_handle(user_id)
    if label == "info":
        return await info_handle(user_id)
    if label == "self_intro":
        return intent.SELF_INTRO
    if label == "affection_guide":
        return await affection_guide.get_guide()
    return None


def _finalize(
    response: str | discord.Embed | tuple[str, discord.Embed], delta: int, current: int
) -> str | discord.Embed | tuple[str, discord.Embed]:
    # embed(또는 embed를 포함한 tuple) 응답에는 이미 호감도가 필드로 보이므로 알림을 따로 안 붙인다.
    if delta == 0 or isinstance(response, (discord.Embed, tuple)):
        return response
    return response + format_affection_notice(delta, current)


async def _apply_emotion_effects(user_id: int, emotion: str) -> int:
    stats = await ensure_daily_stats(user_id)
    updates = {}
    delta = 0

    if emotion in NEGATIVE_EMOTIONS:
        streak_before = stats["negative_emotion_streak"]
        daily_before = stats["negative_emotion_daily_count"]
        if (
            streak_before >= _NEGATIVE_EMOTION_STREAK_THRESHOLD
            or daily_before >= _NEGATIVE_EMOTION_DAILY_THRESHOLD
        ):
            result = await add_affection(user_id, -1)
            delta += result["applied_amount"]
        updates["negative_emotion_streak"] = streak_before + 1
        updates["negative_emotion_daily_count"] = daily_before + 1
    elif stats["negative_emotion_streak"] != 0:
        updates["negative_emotion_streak"] = 0

    if emotion == _HAPPY_EMOTION and not stats["happy_emotion_claimed"]:
        result = await add_affection(user_id, 1, _HAPPY_METHOD)
        delta += result["applied_amount"]
        updates["happy_emotion_claimed"] = True

    if updates:
        await update_daily_stats(user_id, updates)

    return delta
