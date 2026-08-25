from datetime import datetime, timedelta, timezone

import discord

import documents
from command.base import normalize
from core import call_event, intent
from db.affection import add_affection, format_affection_notice
from db.daily_stats import ensure_daily_stats, update_daily_stats
from db.history import get_recent, get_recent_turns, log
from responses.engine import NEGATIVE_EMOTIONS, get_response

# CLAUDE.md 섹션 4-1: 분당 자연어 최대 10회, 초과분 1회당 -1
_RATE_LIMIT_PER_MINUTE = 10
_RATE_LIMIT_WINDOW = timedelta(seconds=60)

# CLAUDE.md 섹션 4-2: 히스토리(30분/최대 50개) 내 누적 3번 반복되면 그다음부터 -1
_HISTORY_WINDOW = timedelta(minutes=30)
_REPEAT_THRESHOLD = 3

# 자연어 생성 시 직전 맥락으로 같이 넣어줄 최근 대화 턴 수 (유저+햄미 답장 합산)
_CONTEXT_TURN_LIMIT = 5

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

    # 음수 호감도면 분류/생성 등 OpenAI API를 아예 호출하지 않고 고정 문구로만 답한다 (섹션 2).
    if affection < 0:
        base = _BITE_RESPONSE if affection <= _BITE_THRESHOLD else _IGNORE_RESPONSE
        return _finalize(base, total_delta, current_affection)

    # RAG 카테고리 분류 + 감정 판정을 한 번의 호출로 처리 (judge 제거, §13-B/C)
    classification = await intent.classify(text)

    if classification.emotion is not None:
        emotion_delta = await _apply_emotion_effects(user_id, classification.emotion)
        total_delta += emotion_delta
        if emotion_delta:
            current_affection += emotion_delta

    context_note = documents.build_context_note(classification.categories)
    context_turns = await get_recent_turns(
        user_id, since=now - _HISTORY_WINDOW, limit=_CONTEXT_TURN_LIMIT
    )
    response_text = await get_response(text, history=context_turns, context_note=context_note)
    await log(user_id, guild_id, response_text, role="assistant")

    return _finalize(response_text, total_delta, current_affection)


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
