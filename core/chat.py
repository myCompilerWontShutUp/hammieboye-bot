import asyncio
import random
from datetime import datetime, timedelta, timezone

import discord

import achievements
import documents
import documents.admin_commands as admin_commands_doc
from admin import console as admin_console
from core.base import normalize
from core import intent
from events import call_event
from events.scheduler import KST, is_within_morning_greeting_window
from events.special_days import DAY_TYPE_BIRTHDAY, get_day_type
from db.achievements import award as award_achievement
from db.affection import add_affection, format_affection_notice
from db.daily_stats import ensure_nl_cap, update_daily_stats
from db.history import get_recent, get_recent_turns, log, set_detected_emotion
from responses.engine import get_admin_command_response, get_response

# 히스토리(30분/최대 50개) 내 누적 3번 반복되면 그다음부터 -1.
_HISTORY_WINDOW = timedelta(minutes=30)
_REPEAT_THRESHOLD = 3

# 반복 발화: 3번째(페널티 전)는 전조 반응, 4번째(페널티 시점)부터는 화난 반응 —
# 둘 다 생성 대신 고정 문구로 답한다(태연한 생성 답변에 하락 알림만 붙으면 어색함).
_REPEAT_WARNING_PHRASES = (
    "같은말 계속하지마! 화낼구야... _(짜증)_",
    "어? 방금도 똑같은 말 했잖아... 자꾸 그러면 삐질 거야. _(삐짐)_",
    "또 똑같은 말이야?? 그만해줘, 진짜로. _(경고)_",
    "잠깐, 그 말 아까도 했잖아!! 이제 그만해줘. _(당황)_",
    "어라?? 벌써 세 번째야... 조금만 다르게 말해줄래. _(갸웃)_",
    "똑같은 말만 하면 햄미 지루해!! 이제 그만. _(지루)_",
    "이러다 진짜 삐질 것 같아... 그만 반복해줘. _(불안)_",
    "같은 말 자꾸 하면 햄미도 힘들어!! _(피곤)_",
    "음... 이거 몇 번째야?? 슬슬 신경 쓰여. _(신경)_",
    "계속 똑같으면 재미없어!! 다른 얘기 해줘. _(시무룩)_",
    "어? 또야?? 이제 진짜 그만했으면 조겠어. _(답답)_",
    "같은 말 세 번째면 좀 그래... 조심해줘. _(걱정)_",
    "자꾸 반복하면 햄미 마음이 쪼그라들어. _(움츠림)_",
    "이번이 딱 세 번째야!! 더는 안 대. _(단호)_",
    "똑같은 말 계속하면 삐질 준비 할 거야. _(경계)_",
    "슬슬 이상해!! 벌써 세 번이나 같은 말이야. _(의아)_",
    "그만 좀!! 계속 같은 말은 재미없어. _(투정)_",
    "이거 반복이지?? 이제 다르게 말해줘. _(눈치)_",
    "세 번째 똑같은 말이야... 조심해줘, 진짜로. _(진지)_",
    "자꾸 같은 말 하면 햄미 삐질 각이야. _(삐죽)_",
)
_REPEAT_ANGRY_PHRASES = (
    "하지말라니깐!! _(화남)_",
    "그만하라고 했잖아!! 진짜 화났어!! _(화남)_",
    "몇 번을 말해야 알아들어!! 그만해!! _(짜증)_",
    "결국 화나버려써!! 같은 말 좀 그만!! _(화남)_",
    "말했잖아!! 이제 진짜 삐졌어!! _(삐짐)_",
    "몇 번째야 이게!! 더는 못 참아!! _(짜증)_",
    "경고했는데도 또 그래!! 실망이야!! _(실망)_",
    "그만하라고 몇 번을 말해!! _(답답)_",
    "이제 완전히 삐져버렸어!! 그만!! _(삐짐)_",
    "진짜 화났다구!! 같은 말 좀 그만해!! _(화남)_",
    "계속 무시하니까 화나잖아!! _(짜증)_",
    "결국 이렇게 되네... 좀 다르게 말해주지!! _(실망)_",
    "말 안 들으면 이렇게 되는 거야!! _(단호)_",
    "햄미 인내심 바닥나써!! 진짜 화났어!! _(화남)_",
    "몇 번째 경고를 무시하는 거야!! _(단호)_",
    "너무해!! 계속 똑같은 말만 하고!! _(서운)_",
    "이제 그만 좀!! 햄미 완전 삐졌어!! _(삐짐)_",
    "같은 말 좀 그만하라고 했잖아!! _(짜증)_",
    "진짜 이럴 거야?? 화나려 그래!! _(화남)_",
    "햄미 삐진 거 안 보여?? 그만해줘!! _(삐짐)_",
)

# 자연어 생성 시 직전 맥락으로 같이 넣어줄 최근 대화 턴 수 (유저+햄미 답장 합산)
_CONTEXT_TURN_LIMIT = 5

# "말풍선 한가득" 업적: 하루 이 횟수 이상 자연어로 대화하면 얻는다.
_SPEECH_BUBBLE_THRESHOLD = 20

# 자연어 대화 일일 상한. 도달 시 API 미호출 고정 문구로만 응답한다. 소진 후 1~4번째
# 시도는 풀 A(아래) 재사용, 5번째는 마지막 경고(풀 B), 6번째부터는 완전 무시 + 매번 -1.
_OVER_CAP_FREE_ATTEMPTS = 4
_OVER_CAP_WARNING_ATTEMPT = 5
_OVER_CAP_IGNORE_RESPONSE = "_(무시)_"

# 상한에 정확히 도달하는 마지막 메시지의 답변 뒤에 이어붙이는 문구 + 소진 후 1~4번째
# 시도에 재사용하는 고정 문구 풀.
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

# 음수 호감도 구간표. 완전 무응답이 아니라 짧은 행동 텍스트로 반응한다.
_BITE_THRESHOLD = -20
_IGNORE_RESPONSE = "(무시)"
_BITE_RESPONSE = "(콱 깨묾)"

_HAPPY_EMOTION = "행복함"
_HAPPY_METHOD = "happy_emotion"

# 감정(20종 강제 분류)의 연속/누적 기반 하락은 폐지 — 애매한 메시지가 부정으로 잘못
# 분류되기만 해도 누적돼 억울하게 깎이는 문제가 있었다. 하락은 이제 심각한 유해 표현
# 감지(has_severe_abuse: 욕설/비방/모욕/성희롱/패드립)에만 연동한다.
_SEVERE_ABUSE_PENALTY = -1

# 햄미 생일 자연어 축하(3-2)/아침 인사(3-6): 둘 다 날짜·시간대로 좁게 게이트되는 1회성
# 판정이라 core/intent.py의 공용 분류 스키마를 확장하지 않고 키워드 매칭으로 독립 처리한다.
_BIRTHDAY_GREETING_REWARD = 10
_BIRTHDAY_GREETING_METHOD = "birthday_greeting"
_BIRTHDAY_KEYWORDS = ("생일", "축하")

_MORNING_GREETING_REWARD = 1
_MORNING_GREETING_METHOD = "morning_greeting"
_MORNING_GREETING_KEYWORDS = ("잘잤", "굿모닝", "좋은아침")


async def handle_natural_language(
    user_id: int, guild_id: int, text: str, affection: int
) -> str | discord.Embed | tuple[str, discord.Embed]:
    now = datetime.now(timezone.utc)

    recent, stats = await asyncio.gather(
        get_recent(user_id, since=now - _HISTORY_WINDOW),
        ensure_nl_cap(user_id, affection),
    )

    total_delta = 0
    current_affection = affection

    def _record(result: dict) -> None:
        nonlocal total_delta, current_affection
        total_delta += result["applied_amount"]
        current_affection = result["new_affection"]

    nl_cap = stats["nl_cap"]
    over_cap = stats["nl_count"] >= nl_cap

    # 정규화 후 비교. recent는 role="user"만 조회되므로 햄미 자신의 답장은 안 섞인다.
    normalized_text = normalize(text)
    repeat_count = sum(1 for row in recent if normalize(row["content"]) == normalized_text)
    is_repeat_penalty = not over_cap and repeat_count >= _REPEAT_THRESHOLD
    is_repeat_warning = not over_cap and repeat_count == _REPEAT_THRESHOLD - 1

    # add_affection의 반환값(new_affection)은 그 순간의 DB 절대값이라, event_delta를
    # 로컬에서 나중에 더하면 이미 반영된 값을 이중으로 더하게 된다 — 반드시 먼저 적용.
    if is_repeat_penalty:
        _record(await add_affection(user_id, -1))

    # 생성 경로에서만 직전 맥락이 필요하고, 이번 메시지를 로그에 남기기 전에 가져와야
    # 프롬프트에 같은 메시지가 중복으로 안 들어간다.
    will_generate = (
        affection >= 0 and not over_cap and not is_repeat_penalty and not is_repeat_warning
    )

    if will_generate:
        context_turns, (event_delta, event_achievement, was_event_response, event_override) = await asyncio.gather(
            get_recent_turns(user_id, since=now - _HISTORY_WINDOW, limit=_CONTEXT_TURN_LIMIT),
            call_event.handle_potential_response(user_id, guild_id, text),
        )
    else:
        context_turns = None
        # 부름 이벤트 응답 판정은 호감도가 음수여도 예외적으로 항상 시도한다.
        event_delta, event_achievement, was_event_response, event_override = await call_event.handle_potential_response(
            user_id, guild_id, text
        )

    logged_row = await log(user_id, guild_id, text)

    if event_delta:
        total_delta += event_delta
        current_affection += event_delta

    # 부름 이벤트 업적 알림은 이후 어떤 분기로 빠지든 최종 응답에 붙어야 한다.
    achievement_notices = [event_achievement] if event_achievement else []

    if affection < 0:
        base = _BITE_RESPONSE if affection <= _BITE_THRESHOLD else _IGNORE_RESPONSE
        return _finalize(base, total_delta, current_affection, achievement_notices)

    if over_cap:
        return await _handle_over_cap(
            user_id, stats, total_delta, current_affection, achievement_notices, was_event_response
        )

    if is_repeat_penalty:
        return _finalize(
            random.choice(_REPEAT_ANGRY_PHRASES), total_delta, current_affection, achievement_notices
        )
    if is_repeat_warning:
        return _finalize(
            random.choice(_REPEAT_WARNING_PHRASES), total_delta, current_affection, achievement_notices
        )

    # 부름 이벤트가 활성 상태인데 관련 없는 잡담이면(-1은 이미 total_delta에 반영됨) 정상
    # 생성을 하지 않고 이 고정 문구로 대체한다(API 미호출, nl_count 미증가).
    if event_override is not None:
        return _finalize(event_override, total_delta, current_affection, achievement_notices)

    # 여기부터 실제 OpenAI API 호출(분류+생성) 구간. 생일/아침 인사 감지는 키워드 매칭이라
    # API 호출과 무관하게 분류와 병렬로 처리한다.
    classification, (greeting_delta, greeting_achievement) = await asyncio.gather(
        intent.classify(text),
        _apply_greeting_bonuses(user_id, text, stats),
    )
    total_delta += greeting_delta
    if greeting_delta:
        current_affection += greeting_delta
    if greeting_achievement:
        achievement_notices.append(greeting_achievement)

    if classification.emotion is not None:
        _, (message_delta, message_achievement) = await asyncio.gather(
            set_detected_emotion(logged_row["id"], classification.emotion),
            _apply_message_effects(
                user_id, classification.emotion, classification.has_severe_abuse, stats
            ),
        )
        total_delta += message_delta
        if message_delta:
            current_affection += message_delta
        if message_achievement:
            achievement_notices.append(message_achievement)

    # 관리자 명령어 자연어 설명: 권한자에게만 답하고, 비권한자는 생성 호출 자체를 안 해서
    # 정보가 새지 않는다. 다른 카테고리와 섞이지 않게 단독 분기로 처리한다.
    if "admin_commands" in classification.categories:
        if not admin_console.is_authorized(user_id):
            return _finalize(
                "너한테는 알려줄 수 없어!!", total_delta, current_affection, achievement_notices
            )
        admin_response = await get_admin_command_response(text, admin_commands_doc.get_text())
        return _finalize(admin_response, total_delta, current_affection, achievement_notices)

    context_note = documents.build_context_note(classification.categories)
    response_text = await get_response(text, history=context_turns, context_note=context_note)

    first_chat_result = await award_achievement(user_id, achievements.first_chat.ID)
    if first_chat_result["earned"]:
        _record(first_chat_result)
        achievement_notices.append(f"🏆 업적 달성: {achievements.format_name(achievements.first_chat)}!!")

    # nl_count는 실제 생성까지 도달한 메시지만 증가시킨다. 상한에 정확히 도달하는
    # 메시지라면 답변 뒤에 고정 문구를 이어붙인다.
    new_nl_count = stats["nl_count"] + 1
    if new_nl_count >= nl_cap:
        response_text = f"{response_text}\n\n{random.choice(_DAILY_LIMIT_PHRASES)}"

    if new_nl_count >= _SPEECH_BUBBLE_THRESHOLD:
        speech_bubble_result = await award_achievement(user_id, achievements.speech_bubble.ID)
        if speech_bubble_result["earned"]:
            _record(speech_bubble_result)
            achievement_notices.append(
                f"🏆 업적 달성: {achievements.format_name(achievements.speech_bubble)}!!"
            )

    await asyncio.gather(
        update_daily_stats(user_id, {"nl_count": new_nl_count}),
        log(user_id, guild_id, response_text, role="assistant"),
    )

    return _finalize(response_text, total_delta, current_affection, achievement_notices)


async def _handle_over_cap(
    user_id: int,
    stats: dict,
    total_delta: int,
    current_affection: int,
    achievement_notices: list[str],
    was_event_response: bool = False,
) -> str | discord.Embed | tuple[str, discord.Embed]:
    # 이 메시지가 부름 이벤트 반응이었다면(긍/부정 무관) 남용 카운터를 건드리지 않는다 —
    # 안 그러면 이벤트 자체의 호감도 변화 위에 남용 페널티까지 겹쳐 붙는다.
    if was_event_response:
        return _finalize(
            random.choice(_DAILY_LIMIT_PHRASES), total_delta, current_affection, achievement_notices
        )

    attempts = stats["over_cap_attempts"] + 1
    await update_daily_stats(user_id, {"over_cap_attempts": attempts})

    if attempts <= _OVER_CAP_FREE_ATTEMPTS:
        return _finalize(
            random.choice(_DAILY_LIMIT_PHRASES), total_delta, current_affection, achievement_notices
        )
    if attempts == _OVER_CAP_WARNING_ATTEMPT:
        return _finalize(
            random.choice(_DAILY_LIMIT_WARNING_PHRASES),
            total_delta,
            current_affection,
            achievement_notices,
        )

    result = await add_affection(user_id, -1)
    total_delta += result["applied_amount"]
    current_affection = result["new_affection"]
    return _finalize(_OVER_CAP_IGNORE_RESPONSE, total_delta, current_affection, achievement_notices)


def _finalize(
    response: str | discord.Embed | tuple[str, discord.Embed],
    delta: int,
    current: int,
    achievement_notices: list[str] | None = None,
) -> str | discord.Embed | tuple[str, discord.Embed]:
    # embed 응답엔 이미 호감도가 필드로 보이므로 알림을 따로 안 붙인다.
    if isinstance(response, (discord.Embed, tuple)):
        return response
    text = response
    if delta != 0:
        text += format_affection_notice(delta, current)
    for notice in achievement_notices or ():
        text += f"\n{notice}"
    return text


async def _apply_greeting_bonuses(user_id: int, text: str, stats: dict) -> tuple[int, str | None]:
    """생일 축하(3-2)/아침 인사(3-6) 자연어 보상. 둘 다 하루 1회, 반복 시엔 추가 지급 없이
    정상 생성 흐름만 그대로 진행한다(생일 쪽은 "이미 줬어" 같은 메타 발언도 없음)."""
    updates = {}
    delta = 0
    achievement_notice = None
    normalized = normalize(text)
    today = datetime.now(KST).date()

    if (
        get_day_type(today) == DAY_TYPE_BIRTHDAY
        and not stats["birthday_greeting_claimed"]
        and all(keyword in normalized for keyword in _BIRTHDAY_KEYWORDS)
    ):
        result = await add_affection(user_id, _BIRTHDAY_GREETING_REWARD, _BIRTHDAY_GREETING_METHOD)
        delta += result["applied_amount"]
        updates["birthday_greeting_claimed"] = True

    if (
        is_within_morning_greeting_window()
        and not stats["morning_greeting_claimed"]
        and any(keyword in normalized for keyword in _MORNING_GREETING_KEYWORDS)
    ):
        result = await add_affection(user_id, _MORNING_GREETING_REWARD, _MORNING_GREETING_METHOD)
        delta += result["applied_amount"]
        updates["morning_greeting_claimed"] = True
        achievement_result = await award_achievement(user_id, achievements.early_bird.ID)
        if achievement_result["earned"]:
            delta += achievement_result["applied_amount"]
            achievement_notice = f"🏆 업적 달성: {achievements.format_name(achievements.early_bird)}!!"

    if updates:
        await update_daily_stats(user_id, updates)

    return delta, achievement_notice


async def _apply_message_effects(
    user_id: int, emotion: str, has_severe_abuse: bool, stats: dict
) -> tuple[int, str | None]:
    updates = {}
    delta = 0
    achievement_notice = None

    if has_severe_abuse:
        result = await add_affection(user_id, _SEVERE_ABUSE_PENALTY)
        delta += result["applied_amount"]

    if emotion == _HAPPY_EMOTION and not stats["happy_emotion_claimed"]:
        result = await add_affection(user_id, 1, _HAPPY_METHOD)
        delta += result["applied_amount"]
        updates["happy_emotion_claimed"] = True
        achievement_notice = result["achievement_notice"]

    if updates:
        await update_daily_stats(user_id, updates)

    return delta, achievement_notice
