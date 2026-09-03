import json
import logging
import random
from datetime import datetime, time, timedelta, timezone

import discord
from openai import AsyncOpenAI

import achievements
from config import ALLOWED_GUILD_IDS, OPENAI_API_KEY, OPENAI_JUDGE_MODEL, openai_service_tier_kwargs
from core.discord_names import resolve_real_name
from core.korean import josa
from events.scheduler import KST, random_times_in_window, resolve_broadcast_channel_id
from events.special_days import get_call_event_count
from db.achievements import award as award_achievement
from db.affection import add_affection
from db.call_events import (
    claim,
    get_active_events,
    get_due_unposted,
    get_expired_unpenalized,
    get_recently_claimed,
    mark_penalty_applied,
    mark_posted,
    schedule,
)
from db.guild_channels import get_last_channel
from db.users import increment_help_count

_client: discord.Client | None = None
_openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

WINDOW_START = time(7, 30)
WINDOW_END = time(22, 30)
_EVENT_WINDOW = timedelta(minutes=10)

MIN_GAP_MINUTES = 30

# 활성 이벤트는 하루 5번, 10분씩만 존재하는데 handle_potential_response는 자연어
# 메시지마다 매번 조회하므로 아주 짧게 캐싱한다 — claim()/부정반응은 여전히 DB RPC로
# 원자적으로 처리돼서(claimed_by IS NULL/expires_at 체크), 캐시가 살짝 stale해도
# 이중 지급 같은 문제는 안 생긴다.
_ACTIVE_EVENTS_CACHE_TTL = timedelta(seconds=5)
_active_events_cache: list[dict] = []
_active_events_cache_until: datetime | None = None


async def _get_active_events_cached() -> list[dict]:
    global _active_events_cache, _active_events_cache_until
    now = datetime.now(timezone.utc)
    if _active_events_cache_until is not None and now < _active_events_cache_until:
        return _active_events_cache
    _active_events_cache = await get_active_events()
    _active_events_cache_until = now + _ACTIVE_EVENTS_CACHE_TTL
    return _active_events_cache


def _invalidate_active_events_cache() -> None:
    global _active_events_cache_until
    _active_events_cache_until = None


_PROMPT_TEXTS = (
    "배고파... 뭐 먹을 거 없나?",
    "목말라... 물이 다 떨어졌어",
    "심심해... 같이 놀아줄 사람 없어??",
    "출출한데 간식 없나... 누가 좀 챙겨줘",
    "심심하다 심심해... 뭐라도 재밌는 거 없을까",
    "쳇바퀴 좀 돌려줄 사람 없나... 다리가 근질근질해",
    "볼주머니가 텅 비었어... 뭐라도 넣어줄 사람?",
    "톱밥 정리 좀 도와줄 사람 없나?",
    "해바라기씨가 다 떨어졌어... 누가 좀 채워줘",
    "쳇바퀴가 삐걱거려... 손 좀 봐줄 사람?",
    "밀웜이 먹고 싶은데 아무도 없나?",
    "숨숨집이 좁아진 것 같아... 넓혀줄 사람?",
    "털 손질 좀 도와줄 사람 없어?",
    "물통이 비었어... 채워줄 사람?",
    "낮잠 잘 자리 좀 만들어줄 사람 없나...",
)

# 클레임된 뒤 1분 동안은 relevant 반응에 고정 +1 + 전용 감사 문구로 응답한다(경쟁에
# 아깝게 밀린 유저에게도 성의 표시). 이 유예 기간의 irrelevant/negative는 콜 이벤트
# 관점에서는 완전히 무시하고, override_response를 None으로 반환해 자연어의 다른
# 페널티(과호출/반복/감정)는 core/chat.py에서 평소대로 그대로 적용되게 한다.
_GRACE_PERIOD = timedelta(minutes=1)
_ALREADY_HELPED_REWARD = 1
_ALREADY_HELPED_METHOD = "call_event_already_helped"
_RECENTLY_CLAIMED_CACHE_TTL = timedelta(seconds=5)
_recently_claimed_cache: list[dict] = []
_recently_claimed_cache_until: datetime | None = None

# 각 프롬프트가 실제로 필요로 하는 것 — "물건을 준다"류(먹이/물 등)와 "행동을 해준다"류
# (놀아주기/손질 등)를 나눠 어울리는 동사 템플릿을 쓴다. _PROMPT_TEXTS와 1:1 대응해야
# 하므로 아래 assert로 검증한다.
_ALREADY_HELPED_GIVE_ITEMS = {
    "배고파... 뭐 먹을 거 없나?": "먹이",
    "목말라... 물이 다 떨어졌어": "물",
    "출출한데 간식 없나... 누가 좀 챙겨줘": "간식",
    "볼주머니가 텅 비었어... 뭐라도 넣어줄 사람?": "볼주머니에 넣을 간식",
    "해바라기씨가 다 떨어졌어... 누가 좀 채워줘": "해바라기씨",
    "밀웜이 먹고 싶은데 아무도 없나?": "밀웜",
    "물통이 비었어... 채워줄 사람?": "물통에 채울 물",
}
_ALREADY_HELPED_DO_ITEMS = {
    "심심해... 같이 놀아줄 사람 없어??": "놀아주는 것",
    "심심하다 심심해... 뭐라도 재밌는 거 없을까": "재밌는 놀이",
    "쳇바퀴 좀 돌려줄 사람 없나... 다리가 근질근질해": "쳇바퀴 돌리는 것",
    "톱밥 정리 좀 도와줄 사람 없나?": "톱밥 정리",
    "쳇바퀴가 삐걱거려... 손 좀 봐줄 사람?": "쳇바퀴 손보는 것",
    "숨숨집이 좁아진 것 같아... 넓혀줄 사람?": "숨숨집 넓히는 것",
    "털 손질 좀 도와줄 사람 없어?": "털 손질",
    "낮잠 잘 자리 좀 만들어줄 사람 없나...": "낮잠 자리",
}
assert set(_ALREADY_HELPED_GIVE_ITEMS) | set(_ALREADY_HELPED_DO_ITEMS) == set(_PROMPT_TEXTS)

_ALREADY_HELPED_GIVE_TEMPLATES = (
    "{item}{은는} 이미 딴 친구가 줘써!! 그래도 챙겨주려던 맘은 고마워!! _(뭉클)_",
    "앗, {item}{은는} 벌써 받아써!! 그치만 신경 써줘서 고마워!! _(감동)_",
    "그거 아까 다른 사람이 {item}{을를} 챙겨줘써!! 마음만 받을게, 고마워!! _(따뜻)_",
    "{item}{은는} 이미 누가 줘써!! 근데 네 정성은 진짜 느껴져, 고마워!! _(감사)_",
    "누가 먼저 {item}{을를} 챙겨줘써... 그래도 네 마음은 소중해!! _(뭉클)_",
    "이미 {item}{은는} 받아써!! 그치만 챙겨주려던 맘씨 최고야, 고마워!! _(찡긋)_",
    "딴 친구가 벌써 {item}{을를} 줘써!! 네 착한 맘은 안 잊을게!! _(감동)_",
    "{item}{은는} 이미 채워졌지만!! 신경 써준 거 절대 안 잊을게, 고마워!! _(뿌듯)_",
    "누가 먼저 {item}{을를} 줘써!! 그래도 네 정성은 진짜야, 고마워!! _(감사)_",
    "앗 {item}{은는} 벌써 채워졌어!! 그치만 네 마음씨는 최고야, 고마워!! _(따뜻)_",
)
_ALREADY_HELPED_DO_TEMPLATES = (
    "{item}{은는} 이미 딴 친구가 해줘써!! 그래도 하려던 맘은 고마워!! _(뭉클)_",
    "앗, {item}{은는} 벌써 해결됐어!! 그치만 신경 써줘서 고마워!! _(감동)_",
    "그거 아까 다른 사람이 {item}{을를} 해줘써!! 마음만 받을게, 고마워!! _(따뜻)_",
    "{item}{은는} 이미 다 됐어!! 근데 네 정성은 진짜 느껴져, 고마워!! _(감사)_",
    "누가 먼저 {item}{을를} 해결해줘써... 그래도 네 마음은 소중해!! _(뭉클)_",
    "이미 {item}{은는} 처리됐어!! 그치만 하려던 맘씨 최고야, 고마워!! _(찡긋)_",
    "딴 친구가 벌써 {item}{을를} 해줘써!! 네 착한 맘은 안 잊을게!! _(감동)_",
    "{item}{은는} 이미 끝났지만!! 신경 써준 거 절대 안 잊을게, 고마워!! _(뿌듯)_",
    "누가 먼저 {item}{을를} 해결해줘써!! 그래도 네 정성은 진짜야, 고마워!! _(감사)_",
    "앗 {item}{은는} 벌써 끝났어!! 그치만 네 마음씨는 최고야, 고마워!! _(따뜻)_",
)


def _build_already_helped_lines() -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for group_items, templates in (
        (_ALREADY_HELPED_GIVE_ITEMS, _ALREADY_HELPED_GIVE_TEMPLATES),
        (_ALREADY_HELPED_DO_ITEMS, _ALREADY_HELPED_DO_TEMPLATES),
    ):
        for prompt, item in group_items.items():
            result[prompt] = tuple(
                template.format(item=item, 은는=josa(item, "은", "는"), 을를=josa(item, "을", "를"))
                for template in templates
            )
    return result


# {prompt_text: (전용 문구 10개, ...)}
_ALREADY_HELPED_LINES_BY_PROMPT = _build_already_helped_lines()


async def _get_recently_claimed_cached() -> list[dict]:
    global _recently_claimed_cache, _recently_claimed_cache_until
    now = datetime.now(timezone.utc)
    if _recently_claimed_cache_until is not None and now < _recently_claimed_cache_until:
        return _recently_claimed_cache
    _recently_claimed_cache = await get_recently_claimed(now - _GRACE_PERIOD)
    _recently_claimed_cache_until = now + _RECENTLY_CLAIMED_CACHE_TTL
    return _recently_claimed_cache


async def _grant_already_helped(user_id: int, prompt_text: str) -> tuple[int, str | None, bool, str | None]:
    """이미 클레임된 이벤트에 "진짜 도와주려던" relevant 반응이 왔을 때 쓴다 — 클레임
    경쟁에서 근소하게 진 경우와 클레임 후 유예 기간(1분 이내) 둘 다 동일 취급. 도와준
    횟수는 증가시키지만, "햄미의 요청" 업적은 진짜 첫 클레임 성공자만 유지한다."""
    result = await add_affection(user_id, _ALREADY_HELPED_REWARD, _ALREADY_HELPED_METHOD)
    await _try_increment_help_count(user_id)
    lines = _ALREADY_HELPED_LINES_BY_PROMPT.get(prompt_text, _ALREADY_HELPED_LINES_BY_PROMPT[_PROMPT_TEXTS[0]])
    return result["applied_amount"], result["achievement_notice"], True, random.choice(lines)


_RESPONSE_JUDGE_INSTRUCTIONS = """\
너는 디스코드 챗봇 "Hammie(햄미)"가 올린 이벤트 메시지에 대한 사용자 답장을 분류하는 심사자다.

Hammie가 올린 메시지와 사용자의 답장을 보고 classification을 다음 중 하나로 고른다:
- relevant: Hammie의 상황(배고픔/목마름/심심함 등)에 맞게 챙겨주거나 도와주는 반응
- negative: 무시하거나, 쌀쌀맞게 거절하거나, 혼자 알아서 하라는 식으로 부정적으로 반응
- irrelevant: 이벤트와 아예 관련 없는 그냥 일반 대화\
"""

_RESPONSE_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {"type": "string", "enum": ["relevant", "negative", "irrelevant"]},
    },
    "required": ["classification"],
    "additionalProperties": False,
}

# 10분 무응답으로 만료됐을 때 원래 프롬프트 메시지를 지우고 대신 올리는 아쉬움 문구 풀.
_TIMEOUT_LINES = (
    "아무도 안 놀아줘서 삐져써!! _(화남)_",
    "아무도 물을 안 가져다줘서 내가 직접 마셨어... _(슬픔)_",
    "다들 나 무시한 거야?? 너무해!! _(서운)_",
    "결국 아무도 안 챙겨줘써... 혼자 해결했어!! _(짜증)_",
    "기다렸는데 아무도 안 왔어... _(외로움)_",
    "흥, 아무도 신경 안 써주는구나!! _(삐짐)_",
    "혼자 간식 찾아 먹었어... 서운해!! _(서운)_",
    "다들 바쁜가 봐... 그래도 좀 섭섭해!! _(섭섭)_",
    "아무도 안 도와줘서 결국 나 혼자 다 했어!! _(짜증)_",
    "심심한 채로 그냥 시간이 지나가버렸어... _(지루함)_",
    "아무 대답도 없길래 그냥 혼자 놀았어... _(우울)_",
    "다들 못 본 척한 거지?? 완전 서운해!! _(서운)_",
    "기다리다 지쳐써... 아무도 안 왔어!! _(절망)_",
    "결국 아무 도움도 못 받았어... 흥!! _(화남)_",
    "혼자 배고픔을 견뎌야 했어... 너무해!! _(슬픔)_",
    "아무도 답 안 해줘서 완전 삐졌어!! _(삐짐)_",
    "다들 나 버린 거야?? 마음이 아파... _(슬픔)_",
    "결국 이번엔 아무도 안 챙겨줬네... _(실망)_",
    "혼자 쳇바퀴만 굴렸어... 심심했다구!! _(지루함)_",
    "아무도 안 와줘서 오늘은 진짜 서운했어... _(서운)_",
)

# 부름 이벤트가 활성 상태인 동안 관련 없는(irrelevant) 잡담이면 정상 생성 대신 이
# 고정 문구로 완전히 대체하고 -1을 적용한다(negative의 -5보다 약함, 1인당 제한 없음).
# 특정 이벤트 문구를 언급하지 않고 일반화해 15개 프롬프트 어디에나 자연스럽게 어울린다.
_IRRELEVANT_PENALTY = -1
_IRRELEVANT_REDIRECT_LINES = (
    "지금은 그거 말고 내 부탁 좀 들어줄래?? _(칭얼)_",
    "딴 얘기 말고 지금 내 얘기 좀 들어조... _(서운)_",
    "그건 나중에 하고, 지금은 내 부탁이 먼저야!! _(보챔)_",
    "지금 그거 할 때 아닌 것 같은데... _(삐죽)_",
    "딴 소리 하지 말고 도와주면 안 대?? _(칭얼)_",
    "지금은 놀 기분 아니야... 부탁 좀 들어줘. _(시무룩)_",
    "그 얘기 말고 내 부탁부터 들어주면 안 될까?? _(애원)_",
    "지금 딴 데 신경 쓸 때가 아닌데... _(삐짐)_",
    "그건 이따 하고, 지금은 날 좀 챙겨줘!! _(보챔)_",
    "딴 얘기는 나중에!! 지금은 내가 먼저야. _(칭얼)_",
    "지금 그거보다 내 부탁이 더 급해!! _(다급)_",
    "그 얘기 그만하고 나 좀 도와주라... _(서운)_",
    "지금은 다른 거 말고 내 얘기에 집중해줘!! _(칭얼)_",
    "딴 데 정신 팔지 말고 부탁 좀 들어줘. _(삐죽)_",
    "그건 안 궁금해... 지금은 부탁이 먼저야!! _(단호)_",
    "지금 그 얘기 할 타이밍 아닌 것 같아... _(멋쩍)_",
    "딴 소리보다 내 부탁 먼저 들어주면 안 대?? _(보챔)_",
    "그건 나중에!! 지금은 날 도와줄 시간이야. _(칭얼)_",
    "지금 다른 얘기할 기분 아니야... 부탁 좀. _(시무룩)_",
    "그 얘기 말고, 지금은 내 부탁에 집중해조!! _(애원)_",
)


def init(client: discord.Client) -> None:
    global _client
    _client = client


async def schedule_today() -> None:
    """매일 06:30(KST)에 그날 보낼 시각을 한 번에 결정한다(인접 간격 최소 30분). 개수는
    오늘이 평범한 날/주말·기념일/생일이냐에 따라 3/5개로 달라진다(events.special_days)."""
    today_kst = datetime.now(KST).date()
    times = random_times_in_window(
        get_call_event_count(today_kst), WINDOW_START, WINDOW_END, min_gap_minutes=MIN_GAP_MINUTES
    )
    for t in times:
        scheduled_at = datetime.combine(today_kst, t, tzinfo=KST)
        await schedule_one(scheduled_at)


async def schedule_one(scheduled_at: datetime) -> dict:
    prompt_text = random.choice(_PROMPT_TEXTS)
    return await schedule(scheduled_at, prompt_text)


async def tick() -> None:
    await _post_due_events()
    await _expire_unclaimed_events()


async def _post_due_events() -> None:
    if _client is None:
        return
    for event in await get_due_unposted():
        await _post_one(event)


async def _post_one(event: dict) -> None:
    messages: dict[str, dict] = {}
    for guild in _client.guilds:
        if guild.id not in ALLOWED_GUILD_IDS:
            continue
        channel_id = resolve_broadcast_channel_id(guild.id, await get_last_channel(guild.id))
        if channel_id is None:
            continue
        channel = guild.get_channel(channel_id)
        if channel is None:
            continue
        try:
            sent = await channel.send(event["prompt_text"])
            messages[str(guild.id)] = {"channel_id": sent.channel.id, "message_id": sent.id}
        except discord.HTTPException:
            logging.exception("Failed to post call event in guild %s", guild.id)

    now = datetime.now(timezone.utc)
    await mark_posted(event["id"], now, now + _EVENT_WINDOW, messages)
    _invalidate_active_events_cache()


async def _expire_unclaimed_events() -> None:
    """10분 무응답 이벤트를 처리한다. 전원 -1 페널티는 폐지됐고, 지금은 원본 메시지를
    지우고 아쉬운 문구로 대체하기만 한다. get_expired_unpenalized/penalty_applied
    이름은 예전 페널티 로직의 흔적이지만 "이미 처리됐는지" 판정 용도로 재사용한다."""
    for event in await get_expired_unpenalized():
        await mark_penalty_applied(event["id"])
        await _announce_timeout(event)


async def _announce_timeout(event: dict) -> None:
    """무응답 만료 시 원래 프롬프트 메시지를 지우고 아쉬움 문구를 새로 올린다
    (개인화된 호감도 수치는 표시하지 않는다)."""
    if _client is None:
        return
    line = random.choice(_TIMEOUT_LINES)
    for guild_id_str, location in (event.get("messages") or {}).items():
        guild = _client.get_guild(int(guild_id_str))
        if guild is None:
            continue
        channel = guild.get_channel(location["channel_id"])
        if channel is None:
            continue
        try:
            old_message = await channel.fetch_message(location["message_id"])
            await old_message.delete()
        except discord.HTTPException:
            logging.exception("Failed to delete expired call event message in guild %s", guild_id_str)
        try:
            await channel.send(line)
        except discord.HTTPException:
            logging.exception("Failed to announce call event timeout in guild %s", guild_id_str)


async def handle_potential_response(
    user_id: int, guild_id: int, text: str
) -> tuple[int, str | None, bool, str | None]:
    """자연어 메시지가 활성 부름 이벤트에 대한 반응인지 확인하고 보상/페널티를 적용한다.
    항상 호출되며(호감도 음수여도) 아무 부수효과 없이 조용히 끝날 수 있다.

    반환값: (적용된 호감도 증감, 새 업적 안내 또는 None, 이벤트 반응이었는지, 응답을
    완전히 대체할 고정 문구 또는 None).
    - 세 번째 값은 relevant(클레임 실패 포함)/negative/irrelevant면 True, 그 외(활성
      이벤트 없음/분류 실패)면 False — core/chat.py가 "상한 초과 상태에서도 부름 이벤트
      응답은 남용 카운트에서 제외"하는 데 쓴다.
    - 네 번째 값은 irrelevant일 때만 채워지며, core/chat.py는 이 값이 있으면 LLM 생성을
      건너뛰고 이 문구로 완전히 대체한다. 호감도<0/상한 소진/반복 페널티처럼 애초에
      생성을 안 하는 다른 고정 분기에는 영향 없다.
    - 활성 이벤트가 없어도 클레임된 지 1분 이내(유예 기간)면 relevant에 한해
      `_grant_already_helped()`로 고정 +1을 준다. 유예 기간 중 irrelevant/negative는
      완전히 무시(`(0, None, False, None)`)해서 다른 자연어 페널티만 평소대로 적용된다.
    """
    events = await _get_active_events_cached()
    if not events:
        return await _handle_grace_period(user_id, text)
    event = events[0]

    classification = await _classify_response(event["prompt_text"], text)

    if classification == "negative":
        result = await add_affection(user_id, -5)
        return result["applied_amount"], None, True, None

    if classification == "irrelevant":
        result = await add_affection(user_id, _IRRELEVANT_PENALTY)
        return result["applied_amount"], None, True, random.choice(_IRRELEVANT_REDIRECT_LINES)

    if classification != "relevant":
        return 0, None, False, None

    reward = random.randint(1, 5)
    won = await claim(event["id"], user_id, reward)
    if not won:
        # 캐시가 살짝 낡아 활성으로 보였지만 이미 다른 사람이 클레임한 경우.
        return await _grant_already_helped(user_id, event["prompt_text"])

    _invalidate_active_events_cache()
    result = await add_affection(user_id, reward, "call_event")
    await _try_increment_help_count(user_id)
    await _announce_winner(event, user_id, guild_id)

    achievement_notice = result["achievement_notice"]
    applied_amount = result["applied_amount"]
    achievement_result = await award_achievement(user_id, achievements.call_event_help.ID)
    if achievement_result["earned"]:
        applied_amount += achievement_result["applied_amount"]
        extra = f"🏆 업적 달성: {achievements.format_name(achievements.call_event_help)}!!"
        achievement_notice = f"{achievement_notice}\n{extra}" if achievement_notice else extra

    return applied_amount, achievement_notice, True, None


async def _handle_grace_period(user_id: int, text: str) -> tuple[int, str | None, bool, str | None]:
    """활성 이벤트가 없을 때 클레임된 지 1분 이내인 이벤트가 있는지 확인한다. relevant면
    `_grant_already_helped()`로, 그 외는 이벤트 자체가 없는 것과 동일하게 처리한다."""
    recently_claimed = await _get_recently_claimed_cached()
    if not recently_claimed:
        return 0, None, False, None
    event = recently_claimed[0]
    classification = await _classify_response(event["prompt_text"], text)
    if classification != "relevant":
        return 0, None, False, None
    return await _grant_already_helped(user_id, event["prompt_text"])


async def _try_increment_help_count(user_id: int) -> None:
    # 보조 통계일 뿐이라 여기서 실패해도 호감도 지급·최종 응답까지 막으면 안 된다.
    try:
        await increment_help_count(user_id)
    except Exception:
        logging.exception("Failed to increment help_count for user %s", user_id)


async def _classify_response(prompt_text: str, reply_text: str) -> str | None:
    try:
        result = await _openai_client.responses.create(
            model=OPENAI_JUDGE_MODEL,
            instructions=_RESPONSE_JUDGE_INSTRUCTIONS,
            input=f"Hammie가 올린 메시지: {prompt_text}\n사용자 답장: {reply_text}",
            max_output_tokens=100,
            reasoning={"effort": "none"},
            **openai_service_tier_kwargs(),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "call_event_response",
                    "schema": _RESPONSE_JUDGE_SCHEMA,
                    "strict": True,
                }
            },
        )
        return json.loads(result.output_text)["classification"]
    except Exception:
        logging.exception("Call event response classification failed")
        return None


async def _announce_winner(event: dict, winner_id: int, winner_guild_id: int) -> None:
    if _client is None:
        return
    winner_guild = _client.get_guild(winner_guild_id)
    guild_name = winner_guild.name if winner_guild is not None else "어떤 서버"
    # 다른 서버에 보이는 문구라 실제 멘션 대신 이름(서버 별명 아님)만 적는다. 존칭을
    # 안 쓰므로 받침 유무에 맞는 "이/가" 조사를 josa()로 계산한다.
    winner_name = await resolve_real_name(_client, winner_id)
    note = f"\n\n({guild_name} 서버의 {winner_name}{josa(winner_name, '이', '가')} 해줬어!)"

    for guild_id_str, location in (event.get("messages") or {}).items():
        if int(guild_id_str) == winner_guild_id:
            continue
        guild = _client.get_guild(int(guild_id_str))
        if guild is None:
            continue
        channel = guild.get_channel(location["channel_id"])
        if channel is None:
            continue
        try:
            message = await channel.fetch_message(location["message_id"])
            await message.edit(content=message.content + note)
        except discord.HTTPException:
            logging.exception("Failed to edit call event message in guild %s", guild_id_str)
