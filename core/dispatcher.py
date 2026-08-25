import logging

import discord

from config import ALLOWED_GUILD_IDS, CALL_PREFIXES
from core import admin, call_event, greeting, onboarding, presence, slash_commands, sleep_event, wake_event
from core.chat import handle_natural_language
from core.client import create_tree
from core.scheduler import is_sleep_time, start_daily, start_interval
from db.daily_stats import increment_messages_today
from db.guild_channels import set_last_channel
from db.users import ensure_user, increment_chat_count, set_consent

_TICK_INTERVAL_SECONDS = 30


def _strip_call_prefix(content: str) -> str | None:
    for prefix in CALL_PREFIXES:
        if content.startswith(prefix):
            return content[len(prefix) :].strip()
    return None


async def _reply(
    message: discord.Message, response: str | discord.Embed | tuple[str, discord.Embed]
) -> None:
    # 누가 무엇을 물어봐서 나온 답인지 구분하기 쉽도록, 항상 답장(reply)으로 보낸다.
    if isinstance(response, tuple):
        text, embed = response
        await message.reply(content=text, embed=embed)
    elif isinstance(response, discord.Embed):
        await message.reply(embed=response)
    else:
        await message.reply(response)


def setup_dispatcher(client: discord.Client) -> None:
    scheduler_started = False
    tree = create_tree(client)
    slash_commands.register(tree)

    @client.event
    async def on_ready() -> None:
        nonlocal scheduler_started
        logging.info("Logged in as %s (id: %s)", client.user, client.user.id)

        # 재연결 시 on_ready가 다시 불릴 수 있어서, 백그라운드 태스크는 최초 1번만 시작한다.
        if scheduler_started:
            return
        scheduler_started = True

        # 슬래시 커맨드는 길드 단위로 등록해서 즉시 반영되게 한다 (§13-A 확정).
        # 길드 하나가 실패(예: applications.commands 스코프 누락)해도 나머지 길드의
        # 동기화와 아래 스케줄러 부트스트랩이 전부 막히면 안 되므로 길드별로 격리한다.
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

        # 봇이 취침 시간대 도중에 켜졌을 수도 있으니, 다음 00:00/06:30을 기다리지 말고 바로 맞춘다.
        await (presence.enter_sleep() if is_sleep_time() else presence.wake_up())

        start_daily(0, 0, presence.enter_sleep)  # 취침 시작: 자리비움 전환
        start_daily(6, 30, presence.wake_up)  # 기상: 온라인 전환
        start_daily(6, 30, call_event.schedule_today)  # 기상 시각: 그날 5개 시각 산출
        start_daily(6, 30, greeting.post_daily_greeting)  # 기상 시각: 아침 인사 + 기념일 언급
        start_daily(23, 59, sleep_event.announce_and_reward)  # 취침 전 최다 대화자 언급
        start_interval(_TICK_INTERVAL_SECONDS, call_event.tick)  # 예정 게시 + 무응답 만료 점검

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.guild is None or message.guild.id not in ALLOWED_GUILD_IDS:
            return
        if admin.is_admin_command(message.content):
            # 관리자 콘솔은 취침 시간대와 무관하게 항상 동작한다 (§13-F 확정).
            await admin.handle(message)
            return
        if is_sleep_time():
            # 취침 시간(00:00~06:30)엔 원칙적으로 무슨 일이 있어도 응답하지 않지만,
            # 봇을 맨션한 경우만 예외로 취침 중 깨움 이벤트 로직을 태운다.
            if client.user in message.mentions:
                await wake_event.handle_mention(message)
            return

        user_message = _strip_call_prefix(message.content)
        if not user_message:
            return

        # 부름/취침/아침 인사 이벤트가 어느 채널에 올릴지는, 유저가 봇을 실제로 부른 채널을 기준으로 정한다.
        await set_last_channel(message.guild.id, message.channel.id)

        user = await ensure_user(message.author.id)
        if not user["consent_given"]:
            if onboarding.is_consent_phrase(user_message):
                await set_consent(message.author.id)
                await message.reply(onboarding.CONFIRMED)
            else:
                await message.reply(onboarding.NOTICE)
            return

        await increment_chat_count(message.author.id)
        await increment_messages_today(message.author.id)

        response = await handle_natural_language(
            message.author.id, message.guild.id, user_message, user["affection"]
        )
        await _reply(message, response)
