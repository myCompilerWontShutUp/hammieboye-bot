import asyncio

import discord
from discord import app_commands

import command.achievements as achievements_view
import command.intro as intro
import command.ranking as ranking
from command.info import handle as info_handle
from command.join import handle as join_handle
from command.join_info import handle as join_info_handle
from command.leave import handle as leave_handle
from command.plastic import handle as plastic_handle
from core import onboarding
from core.base import touch_channel
from db.daily_stats import increment_messages_today
from db.users import get_user
from events import sleep_guard


async def _prepare(interaction: discord.Interaction, *, deferred: bool = True) -> bool:
    """동의 게이트 + 채팅 횟수 집계. 명령어 실행을 진행해도 되면 True.

    deferred=True(기본값)면 호출 시점에 이미 interaction.response.defer()가 끝났다고
    가정하고 미동의 안내를 edit_original_response로 보낸다. deferred=False면 아직 defer 전
    (예: /소개처럼 응답 공개 범위가 갈려서 무거운 작업 직전에야 defer를 결정하는 경우)이라
    response.send_message를 그대로 쓴다.
    """
    # 실제 사용자만 응답 대상이다 — 다른 봇이 이 슬래시 커맨드를 호출한 경우는 무시한다
    # (일반 메시지 경로의 message.author.bot 체크와 동일한 원칙, 확인사항 2).
    if interaction.user.bot:
        return False

    # touch_channel(쓰기)과 get_user(읽기 전용 조회)는 서로 독립적이라 동시에 처리한다
    # (지연시간 최적화). touch_channel 자체가 guild/channel 없음을 안전하게 무시하므로
    # 분기가 필요 없다. 여기서 ensure_user(쓰기)를 쓰지 않는 이유: 미동의 사용자가
    # 이 커맨드를 시도만 해도 DB에 행이 생기는 문제가 있었다 — 실제 동의(/가입)만이
    # 행을 만들어야 한다.
    _, user = await asyncio.gather(
        touch_channel(interaction),
        get_user(interaction.user.id),
    )

    if user is None or not user["consent_given"]:
        # 자연어 경로(개인화 불가)와 경험을 통일하기 위해 공개로 응답한다 (사용자 확정).
        guide = onboarding.random_guide()
        if deferred:
            await interaction.edit_original_response(content=guide)
        else:
            await interaction.response.send_message(guide)
        return False

    # 앞으로 "총 대화한 횟수"(chat_count)는 슬래시 명령어를 제외한다(사용자 확정) — 여기서는
    # daily_stats의 오늘 대화 횟수(messages_today, 3-5 최다 대화자 판정용)만 집계한다.
    await increment_messages_today(interaction.user.id)
    return True


def register(tree: app_commands.CommandTree) -> None:
    @tree.command(name="가입", description="햄미와 친해지기 위해 가입한다")
    async def join_command(interaction: discord.Interaction) -> None:
        await join_handle(interaction)

    @tree.command(name="가입-수집항목", description="가입 시 수집되는 정보를 자세히 안내한다")
    async def join_info_command(interaction: discord.Interaction) -> None:
        await join_info_handle(interaction)

    @tree.command(name="탈퇴", description="햄미와의 관계를 정리하고 탈퇴한다")
    async def leave_command(interaction: discord.Interaction) -> None:
        await leave_handle(interaction)

    @tree.command(name="페트병", description="페트병 던지기 놀이를 한다")
    async def plastic_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        # 취침 중엔 완전 무시하면 디스코드가 "앱이 응답하지 않았어요"를 띄워서 의도한
        # "자는 중" 연출과 다르게 보인다 — 그래서 완전 무시 대신 명시적으로 응답한다
        # (사용자 확정, 2026-08-27). /페트병은 놀이형 커맨드라 메모/수첩 문구 대신 전용
        # SLEEP_REPLY_PLASTIC을 쓴다(사용자 확정, 2026-08-29). defer 전에 바로 응답해야
        # 하므로 guard()가 내부적으로 response.send_message()를 쓴다.
        if not await sleep_guard.guard(interaction, silent=False, message=sleep_guard.SLEEP_REPLY_PLASTIC):
            return
        # 자연어처럼 "생각 중" 표시를 즉시 띄운 뒤, 실제 처리가 끝나면 같은 자리를 결과로
        # 바꿔치기한다 (defer -> edit_original_response, 사용자 확정).
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text = await plastic_handle(interaction.user.id)
        await interaction.edit_original_response(content=text)

    @tree.command(name="내정보", description="내 호감도, 채팅 횟수, 도와준 횟수 등 정보를 확인한다")
    async def info_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed = await info_handle(interaction.user.id, guild=interaction.guild)
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text)
        await interaction.edit_original_response(content=text, embed=embed)

    @tree.command(name="내업적", description="내가 획득한 업적과 아직 못 얻은 업적을 확인한다")
    async def my_achievements_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed = await achievements_view.handle(interaction.user.id)
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text)
        await interaction.edit_original_response(content=text, embed=embed)

    @tree.command(name="랭킹", description="호감도 기준 상위 10명 순위를 확인한다")
    async def ranking_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed = await ranking.build_embed(interaction.client)
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text)
        await interaction.edit_original_response(content=text, embed=embed)

    @tree.command(name="니정보", description="서버 멤버의 정보를 소개한다")
    @app_commands.describe(이름="찾을 사람의 서버 별명(입력하면 자동완성이 떠요) 또는 멘션")
    @app_commands.autocomplete(이름=intro.autocomplete_이름)
    async def intro_command(interaction: discord.Interaction, 이름: str) -> None:
        if interaction.user.bot:
            return
        # /니정보는 "모르는 사람"(개인 전용)과 "찾음"(공개) 응답의 공개 범위가 달라서,
        # 어느 쪽이 될지 모르는 이 시점엔 defer하지 않는다 — intro.handle()이 분기를
        # 확인한 뒤, 무거운 조회 직전에야 defer 여부/공개범위를 스스로 결정한다.
        if not await _prepare(interaction, deferred=False):
            return
        await intro.handle(interaction, 이름)

    @tree.command(name="니업적", description="서버 멤버가 획득한 업적과 아직 못 얻은 업적을 확인한다")
    @app_commands.describe(이름="찾을 사람의 서버 별명(입력하면 자동완성이 떠요) 또는 멘션")
    @app_commands.autocomplete(이름=intro.autocomplete_이름)
    async def intro_achievements_command(interaction: discord.Interaction, 이름: str) -> None:
        if interaction.user.bot:
            return
        if not await _prepare(interaction, deferred=False):
            return
        await intro.handle_achievements(interaction, 이름)
