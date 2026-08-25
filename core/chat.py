import random
from datetime import datetime, timedelta, timezone

import discord

import documents
from command.base import normalize
from core import call_event, intent
from db.affection import add_affection, format_affection_notice
from db.daily_stats import ensure_daily_stats, ensure_nl_cap, update_daily_stats
from db.history import get_recent, get_recent_turns, log
from responses.engine import NEGATIVE_EMOTIONS, get_response

# CLAUDE.md 섹션 4-1: 분당 자연어 최대 10회, 초과분 1회당 -1
_RATE_LIMIT_PER_MINUTE = 10
_RATE_LIMIT_WINDOW = timedelta(seconds=60)

# CLAUDE.md 섹션 4-2: 히스토리(30분/최대 50개) 내 누적 3번 반복되면 그다음부터 -1
_HISTORY_WINDOW = timedelta(minutes=30)
_REPEAT_THRESHOLD = 3

# 반복 발화 대응: 정확히 3번째(아직 페널티 전)엔 전조 반응, 4번째(페널티 시점)부턴 화난 반응으로
# 자연어 생성을 건너뛰고 고정 문구로만 답한다 — 페널티가 실제로 적용되는데 태연한 답이 나오면 어색함.
_REPEAT_WARNING_PHRASES = (
    "같은말 계속하지마! 화낼구야... _(짜증)_",
    "어? 방금도 똑같은 말 했잖아... 자꾸 그러면 삐질 거야. _(삐짐)_",
    "또 똑같은 말이야?? 그만해줘, 진짜로. _(경고)_",
)
_REPEAT_ANGRY_PHRASES = (
    "하지말라니깐!! _(화남)_",
    "그만하라고 했잖아!! 진짜 화났어!! _(화남)_",
    "몇 번을 말해야 알아들어!! 그만해!! _(짜증)_",
)

# 자연어 생성 시 직전 맥락으로 같이 넣어줄 최근 대화 턴 수 (유저+햄미 답장 합산)
_CONTEXT_TURN_LIMIT = 5

# 자연어 대화 일일 상한(신규): 상한 도달 시 API 호출 없이 고정 문구로만 응답한다.
# 상한 소진 후 1~4번째 추가 시도는 풀 A(아래) 재사용, 5번째는 마지막 경고(풀 B),
# 6번째부터는 완전히 무시하며 매번 호감도 -1.
_OVER_CAP_FREE_ATTEMPTS = 4
_OVER_CAP_WARNING_ATTEMPT = 5
_OVER_CAP_IGNORE_RESPONSE = "_(무시)_"

# 상한에 정확히 도달하는 마지막 메시지의 생성 답변 뒤에 이어붙이는 문구 +
# 상한 소진 후 1~4번째 추가 시도에 재사용하는 고정 문구 풀.
_DAILY_LIMIT_PHRASES = (
    "오늘은 너랑 많이 대화해써. 다른 칭구랑 놀고시퍼! 내일바~ _(찡긋)_",
    "햄미 오늘 할 말 다 써버려써!! 낼 또 이야기하자!! _(방긋)_",
    "오늘분 수다는 여기까지야!! 내일 다시 만나조!! _(뿌듯)_",
    "헤헤, 오늘은 이만큼만!! 낼 더 놀아줄게!! _(졸림)_",
    "오늘 얘기 진짜 마니 해써!! 이제 쉬어야 대!! _(피곤)_",
    "햄미 오늘 수다 끝!! 낼 아침에 또 불러조!! _(안녕)_",
    "오늘치 대화는 다 썼어!! 딴 칭구도 만나보고 시퍼!! _(호기심)_",
    "오늘은 여기까지!! 낼 다시 놀아주라!! _(약속)_",
    "햄미 입이 아파써!! 오늘은 이만 자야게써!! _(쉼)_",
    "오늘 대화 한도 끝!! 내일 또 불러줄 거지?? _(기대)_",
    "헥헥, 오늘 진짜 마니 얘기해써!! 낼 보자!! _(숨참)_",
    "오늘은 그만!! 딴 친구랑도 놀고 시퍼!! _(삐죽)_",
    "오늘 몫 다 채워써!! 낼 다시 챗바퀴 돌리고 올게!! _(신남)_",
    "이제 오늘은 조용히 잘래!! 낼 인사하자!! _(꾸벅)_",
    "햄미 오늘 대화 다 써버렸어!! 내일 또 놀아조!! _(찡긋)_",
    "오늘은 여까지 하고 시퍼!! 낼 또 만나조!! _(방실)_",
    "오늘 수다 배 터지게 해써!! 낼 또 오자!! _(배부름)_",
    "이제 다른 칭구도 챙겨야게써!! 낼 다시 와조!! _(바쁨)_",
    "오늘은 여기까지가 딱 조아!! 낼 봐!! _(만족)_",
    "오늘 얘기는 여기서 끝!! 낼 아침에 또 놀자!! _(안녕)_",
)
_DAILY_LIMIT_WARNING_PHRASES = (
    "진짜 마지막이야!! 오늘은 더 이상 말 안 할 거야!! _(단호)_",
    "이게 진짜진짜 마지막 경고야!! 그만 불러줘!! _(경고)_",
    "한 번만 더 부르면 삐질 거야!! 오늘은 끝났다구!! _(삐짐)_",
    "마지막으로 말하는 거야!! 오늘 대화는 다 써버려써!! _(단단)_",
    "이제 진짜 그만!! 낼 다시 놀자니깐!! _(경고)_",
    "마지막 경고야!! 더 부르면 화낼 거야!! _(짜증)_",
    "오늘은 끝이라고 몇 번을 말해!! 이게 마지막이야!! _(답답)_",
    "진짜진짜 마지막이야!! 낼 다시 만나조!! _(경고)_",
    "더 부르면 삐질 거야!! 이게 마지막 기회야!! _(삐죽)_",
    "이번이 진짜 마지막 대답이야!! 그만해줘!! _(단호)_",
    "마지막으로 알려주는 거야!! 오늘은 끝났다구!! _(경고)_",
    "한 번만 더 그러면 진짜 화낼 거야!! 마지막이야!! _(화남)_",
    "이게 마지막 대답이야!! 낼 다시 놀아조!! _(진지)_",
    "더는 못 참아!! 이번이 진짜 마지막이야!! _(경고)_",
    "마지막으로 말할게!! 오늘은 여기까지야!! _(단단)_",
    "한 번 더 부르면 삐질 거니까 마지막이야!! _(삐짐)_",
    "진짜 이게 끝이야!! 더 부르지 마조!! _(단호)_",
    "마지막 기회야!! 이제 그만 불러조!! _(경고)_",
    "이번이 정말 마지막이야!! 낼 아침에 만나조!! _(진지)_",
    "더 부르면 화낼 거야!! 이게 진짜 마지막이야!! _(경고)_",
)

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

    # 자연어 대화 일일 상한(신규) — 06:30에 얼려진 nl_cap을 그대로 쓴다 (재계산하지 않음).
    stats = await ensure_nl_cap(user_id, affection)
    nl_cap = stats["nl_cap"]
    over_cap = stats["nl_count"] >= nl_cap

    # 4-2: 동일 발화 반복 — 정규화 후 비교, 히스토리 내 누적 3번이면 그다음부터 페널티.
    # 상한을 넘긴 뒤로는 완전히 비활성화한다 (사용자 확정).
    normalized_text = normalize(text)
    repeat_count = sum(1 for row in recent if normalize(row["content"]) == normalized_text)
    is_repeat_penalty = not over_cap and repeat_count >= _REPEAT_THRESHOLD  # 4번째부터: 실제 페널티
    is_repeat_warning = not over_cap and repeat_count == _REPEAT_THRESHOLD - 1  # 정확히 3번째: 전조
    if is_repeat_penalty:
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

    # 오늘의 자연어 대화 상한을 이미 다 썼으면, 분류/생성 등 API를 아예 호출하지 않고
    # 고정 문구로만 답한다 (신규).
    if over_cap:
        return await _handle_over_cap(user_id, stats, total_delta, current_affection)

    # 반복 발화 전조/페널티 시점엔 자연어 생성 없이 톤이 맞는 고정 반응으로 답한다 —
    # 태연하게 생성된 답변에 호감도 하락 알림만 붙이면 어색하다 (사용자 피드백).
    if is_repeat_penalty:
        return _finalize(random.choice(_REPEAT_ANGRY_PHRASES), total_delta, current_affection)
    if is_repeat_warning:
        return _finalize(random.choice(_REPEAT_WARNING_PHRASES), total_delta, current_affection)

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

    # nl_count는 실제로 생성까지 도달한 메시지만 증가시킨다. 오늘의 마지막 메시지(상한에
    # 정확히 도달)라면 생성된 답변 뒤에 고정 문구를 이어붙인다 (사용자 예시: "일어나써! + 오늘은...").
    new_nl_count = stats["nl_count"] + 1
    if new_nl_count >= nl_cap:
        response_text = f"{response_text}\n\n{random.choice(_DAILY_LIMIT_PHRASES)}"
    await update_daily_stats(user_id, {"nl_count": new_nl_count})

    await log(user_id, guild_id, response_text, role="assistant")

    return _finalize(response_text, total_delta, current_affection)


async def _handle_over_cap(
    user_id: int, stats: dict, total_delta: int, current_affection: int
) -> str | discord.Embed | tuple[str, discord.Embed]:
    attempts = stats["over_cap_attempts"] + 1
    await update_daily_stats(user_id, {"over_cap_attempts": attempts})

    if attempts <= _OVER_CAP_FREE_ATTEMPTS:
        return _finalize(random.choice(_DAILY_LIMIT_PHRASES), total_delta, current_affection)
    if attempts == _OVER_CAP_WARNING_ATTEMPT:
        return _finalize(
            random.choice(_DAILY_LIMIT_WARNING_PHRASES), total_delta, current_affection
        )

    result = await add_affection(user_id, -1)
    total_delta += result["applied_amount"]
    current_affection = result["new_affection"]
    return _finalize(_OVER_CAP_IGNORE_RESPONSE, total_delta, current_affection)


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
