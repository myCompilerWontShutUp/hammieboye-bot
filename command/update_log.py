import re
from datetime import datetime

import discord
from discord import app_commands

import documents.update_announcement as update_announcement
from core.base import LIST_EMBED_COLOR
from events.scheduler import KST, format_footer_time

# "v1.0.1"/"V1.0.1"/"1.0.1" 전부 허용, 그 외엔 형식 오류로 취급한다.
_VERSION_PATTERN = re.compile(r"^v?(\d+\.\d+\.\d+)$", re.IGNORECASE)

_NO_CHANGES_NOTICE = (
    "이 버전엔 사용자한테 보여줄 만한 변경사항이 없었어 — 내부 개선이나 관리자 기능 "
    "관련 변경뿐이었거든!! _(끄덕)_"
)
_INVALID_FORMAT_MESSAGE = "버전 형식이 이상해!! `v1.0.1`처럼 입력해줘!! _(갸웃)_"
_NOT_FOUND_TEMPLATE = "그런 버전은 없는데?? 확인 가능한 버전은 v{oldest} ~ v{newest}이야!! _(고개 저음)_"

# op 권한자에게만 추가로 붙는 관리자 전용 섹션 — 일반 사용자 응답에는 절대 안 나온다.
_ADMIN_SECTION_HEADER = "\n\n─────────\n🔐 관리자 전용 변경사항"
_ADMIN_SECTION_EMPTY = "이 버전엔 관리자 전용 변경사항이 따로 없었어."


def _normalize(raw: str) -> str | None:
    match = _VERSION_PATTERN.match(raw.strip())
    return match.group(1) if match else None


def _format_date(date: str | None) -> str:
    if date is None:
        return "?"
    return date.replace("-", ".") + "."


def build_response(raw_version: str | None, *, is_op: bool = False) -> discord.Embed | str:
    """성공하면 embed, 버전 형식이 잘못됐거나 존재하지 않으면 안내 문자열을 반환한다.

    is_op=True(관리자 콘솔 권한자, core/slash_commands.py에서 admin.is_authorized()로
    판정)면 기존 내용(일반 사용자와 완전히 동일)을 그대로 두고 그 아래에 구분선 + 관리자
    전용 변경사항 섹션을 추가로 붙인다. 일반 사용자는 이 섹션 자체를 절대 못 본다."""
    versions = update_announcement.known_versions()
    if not versions:
        return "아직 저장된 업데이트 로그가 없어."

    if raw_version is None or not raw_version.strip():
        version = versions[0]
    else:
        version = _normalize(raw_version)
        if version is None:
            return _INVALID_FORMAT_MESSAGE
        if version not in versions:
            return _NOT_FOUND_TEMPLATE.format(oldest=versions[-1], newest=versions[0])

    entries = update_announcement.find_by_version(version)
    changes = [change for entry in entries for change in entry.changes]
    commit_hash = entries[0].commit_hash if entries else "?"
    date_label = _format_date(entries[0].date if entries else None)

    body = "\n".join(f"- {c}" for c in changes) if changes else _NO_CHANGES_NOTICE
    description = f"날짜: {date_label}\n커밋: {commit_hash} (v{version})\n\n{body}"

    if is_op:
        admin_changes = [c for entry in entries for c in entry.admin_changes]
        admin_body = (
            "\n".join(f"- {c}" for c in admin_changes) if admin_changes else _ADMIN_SECTION_EMPTY
        )
        description += f"{_ADMIN_SECTION_HEADER}\n{admin_body}"

    embed = discord.Embed(
        title=f"🔔 업데이트 로그 — v{version}",
        description=description,
        color=LIST_EMBED_COLOR,
    )
    embed.set_footer(text=format_footer_time(datetime.now(KST)))
    return embed


async def autocomplete_버전(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    needle = current.strip().lower().lstrip("v")
    matches = [v for v in update_announcement.known_versions() if needle in v]
    return [app_commands.Choice(name=f"v{v}", value=f"v{v}") for v in matches[:25]]


async def handle(interaction: discord.Interaction, 버전: str | None, *, is_op: bool = False) -> None:
    result = build_response(버전, is_op=is_op)
    if isinstance(result, discord.Embed):
        await interaction.edit_original_response(content=None, embed=result)
    else:
        await interaction.edit_original_response(content=result)
