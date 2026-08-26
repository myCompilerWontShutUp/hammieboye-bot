import json
import logging
import random
from datetime import datetime, time, timedelta, timezone

import discord
from openai import AsyncOpenAI

import achievements
from config import ALLOWED_GUILD_IDS, OPENAI_API_KEY, OPENAI_JUDGE_MODEL
from core.discord_names import resolve_real_name
from core.scheduler import KST, random_times_in_window
from db.achievements import award as award_achievement
from db.affection import add_affection, apply_global_penalty
from db.call_events import (
    claim,
    get_active_events,
    get_due_unposted,
    get_expired_unpenalized,
    mark_penalty_applied,
    mark_posted,
    schedule,
)
from db.guild_channels import get_last_channel
from db.users import increment_help_count

_client: discord.Client | None = None
_openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# CLAUDE.md 섹션 3-2 (2026-08-26: 하루 5회 -> 3회로 축소했다가, 2026-08-27에 5회로 롤백)
_DAILY_EVENT_COUNT = 5
WINDOW_START = time(7, 0)
WINDOW_END = time(23, 0)
_EVENT_WINDOW = timedelta(minutes=10)

# 부름 이벤트 인접 최소 간격(관리자 g-call-event 수동 생성에서도 동일하게 준수, 사용자 확정)
MIN_GAP_MINUTES = 30

# 활성 이벤트는 하루 5번, 10분씩만 존재하는데도 handle_potential_response가 자연어
# 메시지마다 매번 조회하면 대부분의 시간에 낭비되는 DB 왕복이다. 아주 짧게(수 초) 캐싱해서
# 지연시간을 줄인다 — claim()/부정반응 -5는 여전히 DB RPC로 원자적으로 처리되므로(claim의
# claimed_by IS NULL/expires_at 체크가 진짜 정합성 보장 지점), 이 캐시가 살짝 stale해도
# 이중 지급/만료 이벤트 오판정 같은 문제는 생기지 않는다.
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

# 10분 동안 아무도 반응하지 않아 이벤트가 만료됐을 때, 원래 프롬프트 메시지를 지우고 그
# 대신 올리는 고정 문구 풀 (사용자 확정 — 이때는 전원 -1이 적용되지만 특정 유저의 현재
# 호감도는 표시하지 않는다, 전역 방송이라 개인화가 안 맞기 때문).
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


def init(client: discord.Client) -> None:
    global _client
    _client = client


async def schedule_today() -> None:
    """매일 06:30(KST)에 그날 보낼 5개 시각을 한 번에 전부 결정해둔다 (인접 간격 최소 30분 보장)."""
    times = random_times_in_window(
        _DAILY_EVENT_COUNT, WINDOW_START, WINDOW_END, min_gap_minutes=MIN_GAP_MINUTES
    )
    today_kst = datetime.now(KST).date()
    for t in times:
        scheduled_at = datetime.combine(today_kst, t, tzinfo=KST)
        await schedule_one(scheduled_at)


async def schedule_one(scheduled_at: datetime) -> dict:
    """이벤트 하나를 지정 시각에 예약한다. 정규 스케줄링과 관리자 수동 생성(g-call-event) 둘 다 이걸 쓴다 —
    똑같은 방식으로 등록되므로 이후 tick()의 게시/보상/페널티 처리도 실제 이벤트와 완전히 동일하다."""
    prompt_text = random.choice(_PROMPT_TEXTS)
    return await schedule(scheduled_at, prompt_text)


async def tick() -> None:
    """주기적으로 호출: 예정된 이벤트 게시 + 만료된 미응답 이벤트 페널티 처리."""
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
        channel_id = await get_last_channel(guild.id)
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
    _invalidate_active_events_cache()  # 방금 게시됨 → 캐시가 곧바로 "활성 있음"을 반영하도록


async def _expire_unclaimed_events() -> None:
    for event in await get_expired_unpenalized():
        await apply_global_penalty(-1)
        await mark_penalty_applied(event["id"])
        await _announce_timeout(event)


async def _announce_timeout(event: dict) -> None:
    """무응답 만료 시 원래 프롬프트 메시지를 지우고, 그 자리에 실망/서운함이 담긴 고정
    문구를 새로 올린다 (사용자 확정 — 개인화된 호감도 수치는 표시하지 않는다)."""
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


async def handle_potential_response(user_id: int, guild_id: int, text: str) -> tuple[int, str | None]:
    """자연어 메시지 하나가 활성 부름 이벤트에 대한 반응인지 확인하고, 해당하면 보상/페널티를 적용한다.

    이 함수는 항상 호출되며(호감도가 음수여도) 아무 부수효과 없이 조용히 끝날 수 있다.
    반환값은 (이번 호출로 실제 적용된 호감도 증감분, 새로 얻은 업적 안내 문구 또는 None) —
    호출부에서 알림 문구에 합산한다.
    """
    events = await _get_active_events_cached()
    if not events:
        return 0, None
    event = events[0]

    classification = await _classify_response(event["prompt_text"], text)

    if classification == "negative":
        result = await add_affection(user_id, -5)
        return result["applied_amount"], None

    if classification != "relevant":
        return 0, None

    reward = random.randint(1, 10)
    won = await claim(event["id"], user_id, reward)
    if not won:
        return 0, None

    _invalidate_active_events_cache()  # 클레임 완료 → 캐시가 곧바로 "활성 없음"을 반영하도록
    result = await add_affection(user_id, reward, "call_event")
    await _try_increment_help_count(user_id)
    await _announce_winner(event, user_id, guild_id)

    # "햄미의 요청"(처음으로 부름 이벤트에 relevant하게 반응해 도와줌) — add_affection의
    # 호감도 마일스톤 알림과 합쳐서 한 번에 반환한다.
    achievement_notice = result["achievement_notice"]
    if await award_achievement(user_id, achievements.call_event_help.ID):
        extra = f"🏆 업적 달성: {achievements.format_name(achievements.call_event_help)}!!"
        achievement_notice = f"{achievement_notice}\n{extra}" if achievement_notice else extra

    return result["applied_amount"], achievement_notice


async def _try_increment_help_count(user_id: int) -> None:
    """"도와준 횟수" 증가는 보조 통계일 뿐이라, 여기서 실패해도(예: 마이그레이션 미적용으로
    RPC가 아직 없는 경우) 정작 중요한 호감도 지급·최종 답장 전송까지 막으면 안 된다 —
    실제로 이 호출이 그대로 예외를 던지게 뒀다가 add_affection은 이미 적용됐는데 그 뒤
    코드가 전부 중단돼 응답 자체가 안 나가는 사고가 있었다."""
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
    # 다른 서버에 보이는 문구라 실제 멘션(핑) 대신 실제 이름(서버 별명 아님)만 적는다 —
    # "님"을 붙여서 받침 유무와 무관하게 "이/가" 조사를 "님이"로 고정할 수 있다.
    winner_name = await resolve_real_name(_client, winner_id)
    note = f"\n\n({guild_name} 서버의 {winner_name}님이 해줬어!)"

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
