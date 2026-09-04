import asyncio

import discord
from discord import app_commands

import command.achievements as achievements_view
import command.bag as bag_view
import command.bet as bet
import command.coin as coin
import command.eat as eat
import command.intro as intro
import command.ranking as ranking
import command.slot as slot
import command.vending as vending
import command.economy_common as economy_common
from command.economy_common import MAX_BET
from command.info import handle as info_handle
from command.join import handle as join_handle
from command.join_info import handle as join_info_handle
from command.leave import handle as leave_handle
from command.plastic import handle as plastic_handle
from command.vending_catalog import ITEM_NAMES
from core import onboarding
from core.base import touch_channel
from db.daily_stats import increment_messages_today
from db.users import get_user
from events import sleep_guard


async def _prepare(interaction: discord.Interaction, *, deferred: bool = True) -> bool:
    """동의 게이트 + 채팅 횟수 집계. 명령어 실행을 진행해도 되면 True.

    deferred=True(기본값)면 호출 시점에 이미 defer()가 끝났다고 가정하고 미동의 안내를
    edit_original_response로 보낸다. deferred=False면 아직 defer 전(예: /니정보처럼 응답
    공개 범위가 갈려서 무거운 작업 직전에야 defer 여부를 결정하는 경우)이라
    response.send_message를 그대로 쓴다.
    """
    if interaction.user.bot:
        return False

    # get_user는 읽기 전용 — ensure_user(쓰기)를 쓰면 미동의 사용자가 시도만 해도
    # 행이 생겨버린다. 행 생성은 오직 실제 /가입 성공 시에만 일어나야 한다.
    _, user = await asyncio.gather(
        touch_channel(interaction),
        get_user(interaction.user.id),
    )

    if user is None or not user["consent_given"]:
        # 자연어 경로(개인화 불가)와 경험을 통일하기 위해 공개로 응답한다.
        guide = onboarding.random_guide()
        if deferred:
            await interaction.edit_original_response(content=guide)
        else:
            await interaction.response.send_message(guide)
        return False

    # chat_count(총 대화 횟수)는 슬래시 명령어를 제외하므로 여기선 messages_today만 집계한다.
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
        # 완전 무시하면 디스코드가 "앱이 응답하지 않았어요"를 띄워 의도한 연출과 어긋나서
        # 취침 중엔 명시적으로 응답한다. /페트병은 놀이형이라 전용 SLEEP_REPLY_PLASTIC을 쓴다.
        if not await sleep_guard.guard(interaction, silent=False, message=sleep_guard.SLEEP_REPLY_PLASTIC):
            return
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

    @tree.command(name="내업적", description="내가 획득한 업적을 확인한다")
    async def my_achievements_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed = await achievements_view.handle(interaction.user.id)
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text)
        await interaction.edit_original_response(content=text, embed=embed)

    @tree.command(name="랭킹-호감도", description="호감도 기준 상위 10명 순위를 확인한다")
    async def ranking_affection_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed = await ranking.build_affection_embed(interaction.client)
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text)
        await interaction.edit_original_response(content=text, embed=embed)

    @tree.command(name="랭킹-동전", description="동전 보유량 기준 상위 10명 순위를 확인한다")
    async def ranking_coin_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed = await ranking.build_coin_embed(interaction.client)
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text)
        await interaction.edit_original_response(content=text, embed=embed)

    @tree.command(name="니정보", description="서버 멤버의 정보를 소개한다")
    @app_commands.describe(이름="찾을 사람의 서버 별명(입력하면 자동완성이 떠요) 또는 멘션")
    @app_commands.autocomplete(이름=intro.autocomplete_이름)
    async def intro_command(interaction: discord.Interaction, 이름: str) -> None:
        if interaction.user.bot:
            return
        # "모르는 사람"(개인 전용)과 "찾음"(공개) 응답의 공개 범위가 갈려서, intro.handle()이
        # 분기를 확인한 뒤 무거운 조회 직전에야 defer 여부를 스스로 결정한다.
        if not await _prepare(interaction, deferred=False):
            return
        await intro.handle(interaction, 이름)

    @tree.command(name="니업적", description="서버 멤버가 획득한 업적을 확인한다")
    @app_commands.describe(이름="찾을 사람의 서버 별명(입력하면 자동완성이 떠요) 또는 멘션")
    @app_commands.autocomplete(이름=intro.autocomplete_이름)
    async def intro_achievements_command(interaction: discord.Interaction, 이름: str) -> None:
        if interaction.user.bot:
            return
        if not await _prepare(interaction, deferred=False):
            return
        await intro.handle_achievements(interaction, 이름)

    @tree.command(name="내가방", description="내 동전 지갑과 간식 보따리를 확인한다")
    async def bag_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed = await bag_view.handle(interaction.user.id)
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text)
        await interaction.edit_original_response(content=text, embed=embed)

    @tree.command(name="니가방", description="서버 멤버의 동전 지갑과 간식 보따리를 확인한다")
    @app_commands.describe(이름="찾을 사람의 서버 별명(입력하면 자동완성이 떠요) 또는 멘션")
    @app_commands.autocomplete(이름=intro.autocomplete_이름)
    async def intro_bag_command(interaction: discord.Interaction, 이름: str) -> None:
        if interaction.user.bot:
            return
        if not await _prepare(interaction, deferred=False):
            return
        await intro.handle_bag(interaction, 이름)

    @tree.command(name="업적-리스트", description="이 세상 모든 업적의 목록을 확인한다")
    async def achievement_list_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed = await achievements_view.list_handle(interaction.user.id)
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text)
        await interaction.edit_original_response(content=text, embed=embed)

    @tree.command(name="자판기", description="자판기에서 간식이나 동전 지갑 용량 업그레이드를 산다")
    @app_commands.describe(품목="살 물건", 개수="살 개수(기본 1개)")
    @app_commands.choices(품목=[app_commands.Choice(name=n, value=n) for n in ITEM_NAMES])
    async def vending_command(
        interaction: discord.Interaction,
        품목: app_commands.Choice[str],
        개수: int = 1,
    ) -> None:
        if interaction.user.bot:
            return
        if not await sleep_guard.guard(interaction, silent=False):
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        result = await vending.handle_purchase(interaction.user.id, 품목.value, 개수)
        if isinstance(result, tuple):
            text, embed = result
            await interaction.edit_original_response(content=text, embed=embed)
        else:
            await interaction.edit_original_response(content=result)

    @tree.command(name="동전", description="쳇바퀴를 굴려서 동전을 번다")
    async def coin_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        if not await sleep_guard.guard(interaction, silent=False):
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text = await coin.handle(interaction.user.id)
        await interaction.edit_original_response(content=text)

    @tree.command(name="내기-홀짝", description="동전을 걸고 홀짝 내기를 한다")
    @app_commands.describe(동전개수=f"배팅할 동전 개수(최대 {MAX_BET})")
    @app_commands.autocomplete(동전개수=economy_common.autocomplete_동전개수)
    async def bet_odd_even_command(
        interaction: discord.Interaction, 동전개수: app_commands.Range[int, 1, MAX_BET]
    ) -> None:
        if interaction.user.bot:
            return
        if not await sleep_guard.guard(interaction, silent=False):
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        await bet.handle_odd_even(interaction, 동전개수)

    @tree.command(name="내기-가위바위보", description="동전을 걸고 가위바위보 내기를 한다")
    @app_commands.describe(동전개수=f"배팅할 동전 개수(최대 {MAX_BET})")
    @app_commands.autocomplete(동전개수=economy_common.autocomplete_동전개수)
    async def bet_rps_command(
        interaction: discord.Interaction, 동전개수: app_commands.Range[int, 1, MAX_BET]
    ) -> None:
        if interaction.user.bot:
            return
        if not await sleep_guard.guard(interaction, silent=False):
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        await bet.handle_rps(interaction, 동전개수)

    @tree.command(name="슬롯머신", description="동전을 걸고 슬롯머신을 돌린다")
    @app_commands.describe(동전개수=f"배팅할 동전 개수(최대 {MAX_BET})")
    @app_commands.autocomplete(동전개수=economy_common.autocomplete_동전개수)
    async def slot_command(
        interaction: discord.Interaction, 동전개수: app_commands.Range[int, 1, MAX_BET]
    ) -> None:
        if interaction.user.bot:
            return
        if not await sleep_guard.guard(interaction, silent=False):
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        await slot.handle_spin(interaction, 동전개수)

    @tree.command(name="슬롯머신-규칙", description="슬롯머신 배율과 규칙을 확인한다")
    async def slot_rules_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed = await slot.handle_rules()
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text)
        await interaction.edit_original_response(content=text, embed=embed)

    @tree.command(name="먹어", description="디저트 타임에 햄미에게 간식을 먹인다")
    @app_commands.describe(간식="먹일 간식(내 가방에 있는 것만 나와요)")
    @app_commands.autocomplete(간식=eat.autocomplete_간식)
    async def eat_command(interaction: discord.Interaction, 간식: str) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text = await eat.handle(interaction.user.id, 간식)
        await interaction.edit_original_response(content=text)

    @tree.command(name="자판기-리스트", description="자판기에서 파는 물건 전체 목록을 확인한다")
    async def vending_list_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed = await vending.handle_list()
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text)
        await interaction.edit_original_response(content=text, embed=embed)
