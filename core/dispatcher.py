import asyncio
import io
import logging

import discord

from admin import console as admin
from config import ALLOWED_GUILD_IDS, CALL_PREFIXES
from core import onboarding, slash_commands
from core.chat import handle_natural_language
from core.client import create_tree
from db.daily_stats import increment_messages_today, refresh_conversation_caps
from db.guild_channels import set_last_channel
from db.guild_sleep_state import any_triggered_tonight
from db.users import get_user, increment_chat_count
from events import call_event, greeting, presence, sleep_event, wake_event
from events.scheduler import (
    is_late_wake_today,
    is_sleep_time,
    is_sleep_time_for,
    mark_late_wake,
    start_daily,
    start_interval,
)

_TICK_INTERVAL_SECONDS = 30
_LATE_WAKE_DELAY_SECONDS = 30 * 60

# 호출 단어가 확인되면(자고 있을 때 제외) 다른 어떤 DB 조회보다도 먼저 이 플레이스홀더가
# 즉시 떠야 한다 — 그래서 관리는 core/chat.py가 아니라 여기서 한다.
_THINKING_PLACEHOLDER = "_답변중..._"


async def _run_wake_sequence() -> None:
    """매일 06:30(KST) 실행. 그날 밤 방해금지가 발동했으면 기상을 30분 늦추고(07:00)
    아침 인사도 피곤한 톤으로 대체한다. 미발동이면 평소대로 즉시 진행."""
    tired = is_late_wake_today()
    if tired:
        await asyncio.sleep(_LATE_WAKE_DELAY_SECONDS)
    await presence.wake_up()
    await call_event.schedule_today()
    await refresh_conversation_caps()
    await greeting.post_daily_greeting(tired=tired)


def _strip_call_prefix(content: str) -> str | None:
    for prefix in CALL_PREFIXES:
        if content.startswith(prefix):
            return content[len(prefix) :].strip()
    return None


async def _send_placeholder(message: discord.Message) -> discord.Message | None:
    try:
        return await message.reply(_THINKING_PLACEHOLDER)
    except discord.HTTPException:
        logging.exception("Failed to send thinking placeholder")
        return None


async def _delete_placeholder(placeholder: discord.Message | None) -> None:
    if placeholder is None:
        return
    try:
        await placeholder.delete()
    except discord.HTTPException:
        logging.exception("Failed to delete thinking placeholder")


_MAX_MESSAGE_LENGTH = 2000
_TOO_LONG_NOTICE = "내용이 너무 길어서 파일로 첨부했어요!!"


async def _reply(
    message: discord.Message, response: str | discord.Embed | tuple[str, discord.Embed]
) -> None:
    if isinstance(response, tuple):
        text, embed = response
        if len(text) > _MAX_MESSAGE_LENGTH:
            await message.reply(content=_TOO_LONG_NOTICE, embed=embed, file=_as_text_file(text))
        else:
            await message.reply(content=text, embed=embed)
    elif isinstance(response, discord.Embed):
        await message.reply(embed=response)
    elif len(response) > _MAX_MESSAGE_LENGTH:
        await message.reply(_TOO_LONG_NOTICE, file=_as_text_file(response))
    else:
        await message.reply(response)


def _as_text_file(text: str) -> discord.File:
    return discord.File(io.BytesIO(text.encode("utf-8")), filename="result.txt")


def setup_dispatcher(client: discord.Client) -> None:
    scheduler_started = False
    tree = create_tree(client)
    slash_commands.register(tree)

    @client.event
    async def on_ready() -> None:
        nonlocal scheduler_started
        logging.info("Logged in as %s (id: %s)", client.user, client.user.id)

        if scheduler_started:
            return
        scheduler_started = True

        # 길드별로 격리 — 하나가 실패(예: applications.commands 스코프 누락)해도 나머지
        # 동기화와 아래 스케줄러 부트스트랩은 계속돼야 한다.
        for guild_id in ALLOWED_GUILD_IDS:
            guild = discord.Object(id=guild_id)
            try:
                tree.copy_global_to(guild=guild)
                synced = await tree.sync(guild=guild)
                logging.info("Synced %d slash command(s) to guild %s", len(synced), guild_id)
            except discord.HTTPException:
                logging.exception("Failed to sync slash commands to guild %s", guild_id)

        call_event.init(client)
        sleep_event.init(client)
        greeting.init(client)
        presence.init(client)
        admin.init(client)
        await admin.bootstrap()

        # 방해금지(triggered) 여부는 DB에 남지만 presence/지연 기상 플래그는 프로세스
        # 메모리뿐이라 재시작하면 사라진다 — DB 기준으로 복원한다.
        tonight_triggered = await any_triggered_tonight()
        if tonight_triggered:
            mark_late_wake()
        if is_sleep_time():
            await (presence.enter_dnd() if tonight_triggered else presence.enter_sleep())
        else:
            await presence.wake_up()

        start_daily(0, 0, presence.enter_sleep)
        # 기상 시퀀스(온라인 전환+부름 이벤트 산출+nl_cap 동결+아침 인사)를 한 함수로 묶는다
        # — 방해금지 발동 시 전부 30분 늦춰서 함께 실행해야 하기 때문.
        start_daily(6, 30, _run_wake_sequence)
        # 00:00 정각 — 내부에서 "어제" 날짜를 명시적으로 계산하므로 자정 직후에 돌아도 정확하다.
        start_daily(0, 0, sleep_event.announce_and_reward)
        start_interval(_TICK_INTERVAL_SECONDS, call_event.tick)

    @client.event
    async def on_message(message: discord.Message) -> None:
        # 실제 유저만: 봇 계정, 웹훅("앱"으로 표시), 시스템 메시지(입장/고정 등)는 제외.
        # 일반 답장(reply)은 default와 함께 명시적으로 허용해야 한다.
        if message.author.bot or message.webhook_id is not None:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if message.guild is None or message.guild.id not in ALLOWED_GUILD_IDS:
            return

        # "bt set"으로 걸린 이모지 태그는 호출 단어/명령어/취침 시간대와 완전히 무관하게
        # 이 유저의 모든 메시지에 적용된다 — 아래 어떤 분기로 흐르든 상관없이 먼저 처리한다.
        await admin.apply_emoji_tags(message)

        if admin.should_intercept(message):
            await admin.handle(message)
            return
        if is_sleep_time_for(message.channel.id):
            # 취침 중엔 봇 맨션만 예외로 깨움 이벤트를 태운다.
            if client.user in message.mentions:
                await wake_event.handle_mention(message)
            return

        user_message = _strip_call_prefix(message.content)
        if not user_message:
            return

        placeholder = await _send_placeholder(message)
        try:
            # get_user는 읽기 전용 — ensure_user(쓰기)를 쓰면 미동의 사용자가 말만 걸어도
            # 행이 생겨버린다. 행 생성은 오직 실제 /가입 성공 시에만 일어나야 한다.
            _, user = await asyncio.gather(
                set_last_channel(message.guild.id, message.channel.id),
                get_user(message.author.id),
            )
            if user is None or not user["consent_given"]:
                response = onboarding.random_guide()
            else:
                await asyncio.gather(
                    increment_chat_count(message.author.id),
                    increment_messages_today(message.author.id),
                )

                response = await handle_natural_language(
                    message.author.id, message.guild.id, user_message, user["affection"]
                )
        finally:
            await _delete_placeholder(placeholder)

        await _reply(message, response)
