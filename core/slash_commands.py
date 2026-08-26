import asyncio

import discord
from discord import app_commands

from command.info.info import handle as info_handle
from command.plastic.plastic import handle as plastic_handle
from core import intro, membership, onboarding, ranking, sleep_guard
from db.daily_stats import increment_messages_today
from db.guild_channels import set_last_channel
from db.users import ensure_user


async def _prepare(interaction: discord.Interaction, *, silent_during_sleep: bool = False) -> bool:
    """동의 게이트 + 채팅 횟수 집계. 명령어 실행을 진행해도 되면 True."""
    # 실제 사용자만 응답 대상이다 — 다른 봇이 이 슬래시 커맨드를 호출한 경우는 무시한다
    # (일반 메시지 경로의 message.author.bot 체크와 동일한 원칙, 확인사항 2).
    if interaction.user.bot:
        return False

    # 취침 시간대(00:00~06:30)엔 놀이형(/페트병)은 완전히 무시하고, 그 외 시스템형
    # 커맨드는 고정 문구로만 답한다 (사용자 확정). 동의 게이트보다 먼저 확인한다 —
    # 자고 있을 땐 DB 조회조차 하지 않는다.
    if not await sleep_guard.guard(interaction, silent=silent_during_sleep):
        return False

    # set_last_channel과 ensure_user는 서로 독립적인 쓰기라 동시에 처리한다 (지연시간 최적화).
    if interaction.guild is not None and interaction.channel_id is not None:
        _, user = await asyncio.gather(
            set_last_channel(interaction.guild.id, interaction.channel_id),
            ensure_user(interaction.user.id),
        )
    else:
        user = await ensure_user(interaction.user.id)

    if not user["consent_given"]:
        # 자연어 경로(개인화 불가)와 경험을 통일하기 위해 공개로 응답한다 (사용자 확정).
        await interaction.response.send_message(onboarding.random_guide())
        return False

    # 앞으로 "총 대화한 횟수"(chat_count)는 슬래시 명령어를 제외한다(사용자 확정) — 여기서는
    # daily_stats의 오늘 대화 횟수(messages_today, 3-5 최다 대화자 판정용)만 집계한다.
    await increment_messages_today(interaction.user.id)
    return True


def register(tree: app_commands.CommandTree) -> None:
    @tree.command(name="가입", description="햄미와 친해지기 위해 가입한다")
    async def join_command(interaction: discord.Interaction) -> None:
        await membership.handle_join(interaction)

    @tree.command(name="가입-수집항목", description="가입 시 수집되는 정보를 자세히 안내한다")
    async def join_info_command(interaction: discord.Interaction) -> None:
        await membership.handle_join_info(interaction)

    @tree.command(name="탈퇴", description="햄미와의 관계를 정리하고 탈퇴한다")
    async def leave_command(interaction: discord.Interaction) -> None:
        await membership.handle_leave(interaction)

    @tree.command(name="페트병", description="페트병 던지기 놀이를 한다")
    async def plastic_command(interaction: discord.Interaction) -> None:
        if not await _prepare(interaction, silent_during_sleep=True):
            return
        text = await plastic_handle(interaction.user.id)
        await interaction.response.send_message(text)

    @tree.command(name="내정보", description="내 호감도, 채팅 횟수, 도와준 횟수 등 정보를 확인한다")
    async def info_command(interaction: discord.Interaction) -> None:
        if not await _prepare(interaction):
            return
        text, embed = await info_handle(interaction.user.id, guild=interaction.guild)
        await interaction.response.send_message(content=text, embed=embed)

    @tree.command(name="랭킹", description="호감도 기준 상위 5명 순위를 확인한다")
    async def ranking_command(interaction: discord.Interaction) -> None:
        if not await _prepare(interaction):
            return
        text, embed = await ranking.build_embed()
        await interaction.response.send_message(content=text, embed=embed)

    @tree.command(name="소개", description="서버 멤버의 정보를 소개한다")
    @app_commands.describe(이름="찾을 사람의 서버 별명(입력하면 자동완성이 떠요) 또는 멘션")
    @app_commands.autocomplete(이름=intro.autocomplete_이름)
    async def intro_command(interaction: discord.Interaction, 이름: str) -> None:
        if not await _prepare(interaction):
            return
        await intro.handle(interaction, 이름)
