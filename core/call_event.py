import json
import logging
import random
from datetime import datetime, time, timedelta, timezone

import discord
from openai import AsyncOpenAI

from config import ALLOWED_GUILD_IDS, OPENAI_API_KEY, OPENAI_JUDGE_MODEL
from core.scheduler import KST, random_times_in_window
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

_client: discord.Client | None = None
_openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# CLAUDE.md 섹션 3-2
_DAILY_EVENT_COUNT = 5
_WINDOW_START = time(9, 0)
_WINDOW_END = time(21, 0)
_EVENT_WINDOW = timedelta(minutes=10)

_PROMPT_TEXTS = (
    "배고파... 뭐 먹을 거 없나 뾱?",
    "목말라... 물이 다 떨어졌어 뾱",
    "심심해... 같이 놀아줄 사람 없어?? 뾱",
    "출출한데 간식 없나 뾱... 누가 좀 챙겨줘",
    "심심하다 심심해... 뭐라도 재밌는 거 없을까 뾱",
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


def init(client: discord.Client) -> None:
    global _client
    _client = client


async def schedule_today() -> None:
    """매일 08:00(KST)에 그날 보낼 5개 시각을 한 번에 전부 결정해둔다."""
    times = random_times_in_window(_DAILY_EVENT_COUNT, _WINDOW_START, _WINDOW_END)
    today_kst = datetime.now(KST).date()
    for t in times:
        scheduled_at = datetime.combine(today_kst, t, tzinfo=KST)
        prompt_text = random.choice(_PROMPT_TEXTS)
        await schedule(scheduled_at, prompt_text)


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


async def _expire_unclaimed_events() -> None:
    for event in await get_expired_unpenalized():
        await apply_global_penalty(-1)
        await mark_penalty_applied(event["id"])


async def handle_potential_response(user_id: int, guild_id: int, text: str) -> int:
    """자연어 메시지 하나가 활성 부름 이벤트에 대한 반응인지 확인하고, 해당하면 보상/페널티를 적용한다.

    이 함수는 항상 호출되며(호감도가 음수여도) 아무 부수효과 없이 조용히 끝날 수 있다.
    반환값은 이번 호출로 실제 적용된 호감도 증감분(없으면 0) — 호출부에서 알림 문구에 합산한다.
    """
    events = await get_active_events()
    if not events:
        return 0
    event = events[0]

    classification = await _classify_response(event["prompt_text"], text)

    if classification == "negative":
        result = await add_affection(user_id, -5)
        return result["applied_amount"]

    if classification != "relevant":
        return 0

    reward = random.randint(1, 10)
    won = await claim(event["id"], user_id, reward)
    if not won:
        return 0

    result = await add_affection(user_id, reward, "call_event")
    await _announce_winner(event, user_id, guild_id)
    return result["applied_amount"]


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
    note = f"\n\n({guild_name} 서버의 <@{winner_id}>가 해줬어!)"

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
