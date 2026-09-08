import asyncio

import discord
from discord import app_commands

from admin import console as admin
import command.achievements as achievements_view
import command.bet as bet
import command.black_market as black_market
import command.coin as coin
import command.intro as intro
import command.probability_info as probability_info
import command.ranking as ranking
import command.slot as slot
import command.use as use_item
import command.vending as vending
from command.collection_info import handle as collection_info_handle
from command.info import handle_self as info_handle_self
from command.leave import handle as leave_handle
from command.plastic import handle as plastic_handle
from command.update_log import autocomplete_버전, handle as update_log_handle
from core import onboarding
from core.base import touch_channel
from db.daily_stats import increment_messages_today
from events import sleep_guard


async def _prepare(interaction: discord.Interaction, *, deferred: bool = True) -> bool:
    """자동 등록 + 채팅 횟수 집계. 명령어 실행을 진행해도 되면 True.

    2026-09-08부로 별도 동의(/가입) 절차가 폐지되어, 처음 시도하는 순간
    onboarding.provision()이 즉시 유저 행을 만든다 — 유일하게 막히는 경우는 /탈퇴
    직후 30일 쿨타임(provision()이 (None, 안내 문구)를 돌려준다). 슬래시 커맨드
    경로는 자연어 경로(core/dispatcher.py::on_message, 이쪽은 완전 무응답으로 처리)와
    달리 이 안내 문구를 그대로 보여준다.

    deferred=True(기본값)면 호출 시점에 이미 defer()가 끝났다고 가정하고 차단 안내를
    edit_original_response로 보낸다. deferred=False면 아직 defer 전(예: /니정보처럼 응답
    공개 범위가 갈려서 무거운 작업 직전에야 defer 여부를 결정하는 경우)이라
    response.send_message를 그대로 쓴다.
    """
    if interaction.user.bot:
        return False

    _, (user, block_message) = await asyncio.gather(
        touch_channel(interaction),
        onboarding.provision(interaction.user.id),
    )

    if user is None:
        if deferred:
            await interaction.edit_original_response(content=block_message)
        else:
            await interaction.response.send_message(block_message)
        return False

    # chat_count(총 대화 횟수)는 슬래시 명령어를 제외하므로 여기선 messages_today만 집계한다.
    await increment_messages_today(interaction.user.id)
    return True


def register(tree: app_commands.CommandTree) -> None:
    @tree.command(name="봇정보-수집항목", description="햄미가 저장하는 정보를 안내한다")
    async def collection_info_command(interaction: discord.Interaction) -> None:
        await collection_info_handle(interaction)

    @tree.command(name="봇정보-확률공개", description="확률형 콘텐츠의 확률을 안내한다")
    async def probability_info_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer(ephemeral=True)
        await touch_channel(interaction)
        await interaction.edit_original_response(embed=probability_info.build_embed())

    @tree.command(name="탈퇴", description="햄미가 모은 내 정보를 삭제한다")
    async def leave_command(interaction: discord.Interaction) -> None:
        await leave_handle(interaction)

    @tree.command(name="페트병", description="페트병을 던진다")
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

    @tree.command(name="내정보", description="내 호감도·가방·업적·기록을 확인한다")
    async def info_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed, view = await info_handle_self(interaction)
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text, notebook=True)
        await interaction.edit_original_response(content=text, embed=embed, view=view)
        view.message = await interaction.original_response()

    @tree.command(name="랭킹", description="호감도·동전 순위를 확인한다")
    async def ranking_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed, view = await ranking.handle(interaction.user.id, interaction.client)
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text)
        await interaction.edit_original_response(content=text, embed=embed, view=view)
        view.message = await interaction.original_response()

    @tree.command(name="니정보", description="서버 멤버를 소개한다")
    @app_commands.describe(이름="찾을 사람의 서버 별명(자동완성) 또는 멘션")
    @app_commands.autocomplete(이름=intro.autocomplete_이름)
    async def intro_command(interaction: discord.Interaction, 이름: str) -> None:
        if interaction.user.bot:
            return
        # "모르는 사람"(개인 전용)과 "찾음"(공개) 응답의 공개 범위가 갈려서, intro.handle()이
        # 분기를 확인한 뒤 무거운 조회 직전에야 defer 여부를 스스로 결정한다.
        if not await _prepare(interaction, deferred=False):
            return
        await intro.handle(interaction, 이름)

    @tree.command(name="업적-리스트", description="전체 업적 목록을 확인한다")
    async def achievement_list_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed, view = await achievements_view.list_handle(interaction.user.id)
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text)
        await interaction.edit_original_response(content=text, embed=embed, view=view)
        view.message = await interaction.original_response()

    @tree.command(name="자판기", description="자판기에서 물건을 산다")
    async def vending_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        if not await sleep_guard.guard(interaction, silent=False, message=sleep_guard.SLEEP_REPLY_VENDING):
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed, view = await vending.handle(interaction.user.id)
        await interaction.edit_original_response(content=text, embed=embed, view=view)
        view.message = await interaction.original_response()

    @tree.command(name="암시장", description="밤에만 몰래 열리는 상점에서 거래한다")
    async def black_market_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        # /자판기와 정반대 게이트 — 오직 취침 시간대에만 열린다("햄미가 몰래 일어나
        # 거래한다"는 컨셉, events/sleep_guard.py::guard_sleep_only 참고).
        if not await sleep_guard.guard_sleep_only(interaction, message=black_market.DAYTIME_BLOCK_MESSAGE):
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text, embed, view = await black_market.handle(interaction.user.id)
        await interaction.edit_original_response(content=text, embed=embed, view=view)
        view.message = await interaction.original_response()

    @tree.command(name="동전", description="쳇바퀴를 굴려서 동전을 번다")
    async def coin_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        if not await sleep_guard.guard(interaction, silent=False, message=sleep_guard.SLEEP_REPLY_COIN):
            return
        await interaction.response.defer()
        if not await _prepare(interaction):
            return
        text = await coin.handle(interaction.user.id)
        await interaction.edit_original_response(content=text)

    @tree.command(name="내기", description="동전을 걸고 내기를 한다")
    async def bet_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        if not await sleep_guard.guard(interaction, silent=False, message=sleep_guard.SLEEP_REPLY_BET):
            return
        await interaction.response.defer(ephemeral=True)
        if not await _prepare(interaction):
            return
        await bet.handle_bet(interaction)

    @tree.command(name="내기-규칙", description="내기 규칙을 확인한다")
    async def bet_rules_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer(ephemeral=True)
        if not await _prepare(interaction):
            return
        text, embed, view = await bet.handle_rules()
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text)
        await interaction.edit_original_response(content=text, embed=embed, view=view)
        view.interaction = interaction

    @tree.command(name="도박", description="동전을 걸고 도박을 한다")
    async def gamble_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        # /내기와 달리 취침 시간대에도 차단하지 않는다(2026-09-06) — slot.handle_gamble이
        # 대신 인트로 문구만 SLEEP_REPLY_GAMBLE로 바꿔치기한다.
        await interaction.response.defer(ephemeral=True)
        if not await _prepare(interaction):
            return
        await slot.handle_gamble(interaction)

    @tree.command(name="도박-규칙", description="도박 규칙을 확인한다")
    async def gamble_rules_command(interaction: discord.Interaction) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer(ephemeral=True)
        if not await _prepare(interaction):
            return
        text, embed, view = await slot.handle_rules()
        text = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text)
        await interaction.edit_original_response(content=text, embed=embed, view=view)
        view.interaction = interaction

    @tree.command(name="사용", description="가방에 있는 아이템을 사용한다")
    @app_commands.describe(아이템="사용할 아이템")
    @app_commands.autocomplete(아이템=use_item.autocomplete_아이템)
    async def use_command(interaction: discord.Interaction, 아이템: str) -> None:
        if interaction.user.bot:
            return
        # 아이템에 따라 응답 공개 범위가 갈려서(간식=공개, 금서·햄미 일정표=ephemeral),
        # /니정보와 동일하게 use_item.handle()이 아이템을 먼저 확인한 뒤 defer 여부를
        # 스스로 결정한다.
        if not await _prepare(interaction, deferred=False):
            return
        await use_item.handle(interaction, interaction.user.id, 아이템)

    @tree.command(name="업데이트-로그", description="지난 업데이트 내역을 버전별로 확인한다")
    @app_commands.describe(버전="확인할 버전(예: v1.0.1), 생략하면 최신 버전")
    @app_commands.autocomplete(버전=autocomplete_버전)
    async def update_log_command(
        interaction: discord.Interaction, 버전: str | None = None
    ) -> None:
        if interaction.user.bot:
            return
        await interaction.response.defer(ephemeral=True)
        if not await _prepare(interaction):
            return
        # op 권한자는 기존 내용에 이어 관리자 전용 변경사항 섹션을 추가로 본다
        # (command/update_log.py::build_response, 2026-09-08 신규 — 일반 사용자는
        # 이 섹션 자체가 안 붙는다).
        await update_log_handle(interaction, 버전, is_op=admin.is_authorized(interaction.user.id))
