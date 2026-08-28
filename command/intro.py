import random
import re

import discord
from discord import app_commands

import command.achievements as achievements_view
from command.info import handle as info_handle
from events import sleep_guard
from db.users import get_user

_MENTION_RE = re.compile(r"^<@!?(\d+)>$")
_MAX_SUGGESTIONS = 25

# 찾는 사람을 못 찾았을 때(또는 아직 가입 안 한 사람일 때) 보여주는 개인 전용 안내.
_UNKNOWN_LINES = (
    "그런 사람은 모르겠는데?? _(갸웃)_",
    "음... 처음 듣는 이름이야!! _(고민)_",
    "그 사람은 잘 모르겠어!! 다시 찾아봐줄래?? _(미안)_",
    "어라, 그런 이름은 못 찾겠어!! _(갸웃)_",
    "그 사람 아직 나랑 안 친한가 봐!! _(아쉬움)_",
    "누군지 잘 모르겠어... 목록에서 골라줄래?? _(부끄)_",
    "그런 사람은 없는 것 같아!! _(갸웃)_",
    "음, 낯선 이름이야!! 다시 확인해줄래?? _(궁금)_",
    "그 사람은 햄미가 잘 몰라!! _(미안)_",
    "어... 누구야?? 처음 들어봐!! _(당황)_",
    "그런 이름은 찾을 수 없었어!! _(속상)_",
    "잘 모르는 사람이야... 자동완성 목록에서 골라줄래?? _(부탁)_",
    "그 사람 아직 못 만나본 것 같아!! _(아쉬움)_",
    "이름이 헷갈려!! 목록에서 다시 골라줄래?? _(당황)_",
    "그런 사람은 기억에 없어!! _(갸웃)_",
    "음... 확실하지 않아!! 다시 시도해줄래?? _(고민)_",
    "그 사람은 아직 나랑 인연이 없나 봐!! _(서운)_",
    "누군지 못 찾겠어!! 이름을 다시 확인해줘!! _(미안)_",
    "그런 이름의 사람은 안 보여!! _(갸웃)_",
    "잘 모르는 사람인가 봐!! 목록에서 선택해줄래?? _(부탁)_",
)


async def autocomplete_이름(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """서버 별명이 입력값으로 시작하는 멤버들을 실시간으로 제안한다 (최대 25개, Discord 제한).
    선택지의 표시 이름에 실제 디스코드 이름을 같이 보여줘서 별명이 겹쳐도 구분할 수 있게 한다."""
    guild = interaction.guild
    if guild is None:
        return []

    query = current.strip().lower()
    choices: list[app_commands.Choice[str]] = []
    for member in guild.members:
        if member.bot:
            continue
        if query and not member.display_name.lower().startswith(query):
            continue
        label = f"{member.display_name} ({member})"
        if len(label) > 100:
            label = label[:97] + "..."
        choices.append(app_commands.Choice(name=label, value=str(member.id)))
        if len(choices) >= _MAX_SUGGESTIONS:
            break
    return choices


def _resolve_member(guild: discord.Guild, raw: str) -> discord.Member | None:
    raw = raw.strip()
    if raw.isdigit():
        return guild.get_member(int(raw))

    mention_match = _MENTION_RE.match(raw)
    if mention_match:
        return guild.get_member(int(mention_match.group(1)))

    # 자동완성 없이 이름을 그대로 타이핑한 경우 — 정확히 일치하는 사람이 딱 1명일 때만 허용
    # (여러 명이면 어차피 자동완성 목록에서 골라야 하므로 여기서 임의로 고르지 않는다).
    exact = [
        member
        for member in guild.members
        if not member.bot and member.display_name.lower() == raw.lower()
    ]
    return exact[0] if len(exact) == 1 else None


async def _resolve_target(interaction: discord.Interaction, 이름: str) -> discord.Member | None:
    """대상자를 찾아 검증까지 마친다. 못 찾았거나(또는 미가입) "모르는 사람" 개인 전용
    안내를 이미 보낸 상태로 None을 반환하고, 찾았으면 그 Member를 반환한다(응답은 아직
    안 보낸 상태 — 호출부가 defer 이후 실제 내용을 채운다)."""
    guild = interaction.guild
    if guild is None:
        return None

    member = _resolve_member(guild, 이름)
    if member is None or member.bot:
        await interaction.response.send_message(random.choice(_UNKNOWN_LINES), ephemeral=True)
        return None

    target = await get_user(member.id)
    if target is None or not target["consent_given"]:
        # 찾은 사람이 아직 /가입을 안 한 경우도 "모르는 사람" 취급과 동일하게 안내한다.
        await interaction.response.send_message(random.choice(_UNKNOWN_LINES), ephemeral=True)
        return None

    return member


async def handle(interaction: discord.Interaction, 이름: str) -> None:
    """/니정보: "모르는 사람"(개인 전용) 응답과 "찾음"(공개) 응답은 공개 범위가 서로 달라서,
    이 함수가 분기를 다 확인한 뒤에야 defer 여부/공개 범위를 스스로 결정한다 — 그래야 무거운
    조회(info_handle, 실제 이름 조회)에만 "생각 중" 표시가 붙고, 가벼운 실패 분기는 그대로
    즉시 응답한다(지연시간 최적화, 두 응답의 ephemeral이 서로 달라 defer를 미리 걸 수 없기도
    하다)."""
    member = await _resolve_target(interaction, 이름)
    if member is None:
        return
    guild = interaction.guild

    # 여기서부터가 실제로 느린 구간(info_handle의 여러 DB 호출 + 실제 이름 조회)이라 defer한다.
    await interaction.response.defer()
    text, embed = await info_handle(member.id, target_name=member.display_name, guild=guild)
    # 취침 중엔 수첩 문구로만 답하고, 깨어있으면 text를 그대로 쓴다 — text(_INTRO_OTHER_LINES)가
    # 이미 대상자 이름을 문장 안에 자연스럽게 포함하고 있으므로, 예전처럼 실제 이름을 앞에
    # 따로 붙이면 "서희\n서희를 한번 살펴볼까??"처럼 이름이 중복 노출되는 버그가 된다
    # (사용자 발견·확정, 2026-08-28 — §32-6에서 취침 중 노출만 고치고 평시 중복은 놓쳤었다).
    content = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text, notebook=True)
    await interaction.edit_original_response(content=content, embed=embed)


async def handle_achievements(interaction: discord.Interaction, 이름: str) -> None:
    """/니업적: /니정보와 동일한 대상자 해석·가시성·취침 처리 패턴을 그대로 따르되,
    보여주는 내용만 업적 임베드로 바꾼다."""
    member = await _resolve_target(interaction, 이름)
    if member is None:
        return

    await interaction.response.defer()
    text, embed = await achievements_view.handle(member.id, target_name=member.display_name)
    # /니정보와 동일한 이유로 real_name 접두어를 붙이지 않는다 — achievements_view의
    # _INTRO_OTHER_LINES도 이미 대상자 이름을 문장 안에 포함하고 있다.
    content = sleep_guard.wrap_text_if_asleep(interaction.channel_id, text, notebook=True)
    await interaction.edit_original_response(content=content, embed=embed)
