import asyncio
import logging

import discord

from config import ALLOWED_GUILD_IDS, CALL_PREFIXES
from core import admin, call_event, greeting, onboarding, presence, slash_commands, sleep_event, wake_event
from core.chat import handle_natural_language
from core.client import create_tree
from core.scheduler import (
    is_late_wake_today,
    is_sleep_time,
    is_sleep_time_for,
    mark_late_wake,
    start_daily,
    start_interval,
)
from db.daily_stats import increment_messages_today, refresh_conversation_caps
from db.guild_channels import set_last_channel
from db.guild_sleep_state import any_triggered_tonight
from db.users import ensure_user, increment_chat_count

_TICK_INTERVAL_SECONDS = 30
_LATE_WAKE_DELAY_SECONDS = 30 * 60


async def _run_wake_sequence() -> None:
    """매일 06:30(KST)에 실행. 그날 밤 방해금지 이벤트가 발동했으면(§28) 기상 자체를
    30분 늦춰서(07:00) "누가 깨워서 늦게 일어났다"는 컨셉을 실제로 반영하고, 아침 인사도
    피곤한 톤의 고정 문구로 대체한다. 발동 안 했으면 평소대로 즉시 진행."""
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
        admin.init(client)

        # 봇이 취침 시간대 도중에 켜졌을 수도 있으니, 다음 00:00/06:30을 기다리지 말고 바로 맞춘다.
        # 방해금지(triggered) 여부는 DB에 남아있지만 presence와 §28의 지연 기상 플래그는
        # 프로세스 메모리뿐이라 재배포/재시작하면 사라진다 — 여기서 DB 기준으로 복원한다
        # (실사용 중 발견: PR 배포로 재시작하니 방해금지 상태였던 게 "쿨쿨 자는 중"으로
        # 되돌아가 보였던 버그).
        tonight_triggered = await any_triggered_tonight()
        if tonight_triggered:
            mark_late_wake()
        if is_sleep_time():
            await (presence.enter_dnd() if tonight_triggered else presence.enter_sleep())
        else:
            await presence.wake_up()

        start_daily(0, 0, presence.enter_sleep)  # 취침 시작: 자리비움 전환
        # 기상(온라인 전환 + 부름 이벤트 5개 시각 산출 + nl_cap 동결 + 아침 인사)을 한
        # 시퀀스로 묶는다 — 방해금지 발동 시 전부 30분 늦춰서 함께 실행해야 하기 때문(§28).
        start_daily(6, 30, _run_wake_sequence)
        # 자정을 살짝 넘긴 00:01에 실행 — 23:59에 하면 "11시 59분에 자러 감을 선언"하는
        # 꼴이 되어 사용자 확정대로 자정(00:00) 이후로 옮겼다. 함수 내부에서 "어제" 날짜를
        # 명시적으로 계산해서 집계하므로 자정을 넘긴 뒤 실행돼도 정확하다.
        start_daily(0, 1, sleep_event.announce_and_reward)  # 최다 대화자 발표 + 전원 보상
        start_interval(_TICK_INTERVAL_SECONDS, call_event.tick)  # 예정 게시 + 무응답 만료 점검

    @client.event
    async def on_message(message: discord.Message) -> None:
        # 실제 사용자에게만 응답한다 — 봇 계정(message.author.bot)뿐 아니라 웹훅으로 온
        # 메시지("앱"으로 표시되는 것들, message.author.bot이 항상 True로 뜨리라는 보장이
        # 없어 명시적으로 한 번 더 확인)와 디스코드 시스템 메시지(입장/고정/부스트 알림 등)도
        # 전부 걸러낸다. 일반 답장(reply)은 MessageType.reply라 default와 함께 허용해야 한다.
        if message.author.bot or message.webhook_id is not None:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if message.guild is None or message.guild.id not in ALLOWED_GUILD_IDS:
            return
        if admin.should_intercept(message):
            # 관리자 콘솔은 취침 시간대와 무관하게 항상 동작한다 (§13-F 확정).
            await admin.handle(message)
            return
        if is_sleep_time_for(message.author.id):
            # 취침 시간(00:00~06:30)엔 원칙적으로 무슨 일이 있어도 응답하지 않지만,
            # 봇을 맨션한 경우만 예외로 취침 중 깨움 이벤트 로직을 태운다. (관리자가
            # hm-awake/hm-asleep으로 강제 오버라이드한 경우 실제 시간 대신 그 값을 따른다.)
            if client.user in message.mentions:
                await wake_event.handle_mention(message)
            return

        user_message = _strip_call_prefix(message.content)
        if not user_message:
            return

        # 부름/취침/아침 인사 이벤트가 어느 채널에 올릴지는, 유저가 봇을 실제로 부른 채널을 기준으로
        # 정한다. ensure_user와는 서로 독립적인 쓰기라 동시에 처리한다 (지연시간 최적화).
        _, user = await asyncio.gather(
            set_last_channel(message.guild.id, message.channel.id),
            ensure_user(message.author.id),
        )
        if not user["consent_given"]:
            # 동의(가입)는 이제 오직 `/가입` 슬래시 커맨드로만 가능하다 — 자연어 문구 인정 폐기.
            await message.reply(onboarding.random_guide())
            return

        await asyncio.gather(
            increment_chat_count(message.author.id),
            increment_messages_today(message.author.id),
        )

        response = await handle_natural_language(
            message.author.id, message.guild.id, user_message, user["affection"], message
        )
        await _reply(message, response)
