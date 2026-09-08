import discord

from core.base import touch_channel
from db.history import RETENTION_DAYS

# 2026-09-08 舊 /가입-수집항목에서 개명 — 별도 동의(/가입) 절차가 폐지되면서 더 이상
# "가입하면"이라는 전제가 안 맞아졌고, 언제든 조회 가능한 독립 안내 명령어가 됐다.
# 실제 채팅 원문 보관 기간(이전엔 고지되지 않던 부분)을 명시하고, 삭제 방법(/탈퇴)도
# 안내한다.
_COLLECTION_NOTICE = (
    "햄미가 저장하는 정보는 이래!!\n"
    "- 채팅 횟수 · 도와준 횟수 · 호감도\n"
    "- 동전 · 간식 · 업적 등 게임 관련 기록\n"
    f"- 최근 대화 내용 (최대 {RETENTION_DAYS}일 보관 후 자동 삭제)\n"
    "- 처음 상호작용한 날짜\n\n"
    "서버가 달라도 같은 사람이면 하나로 합쳐서 관리해. "
    "이 정보를 지우고 싶으면 언제든 `/탈퇴`를 이용해줘!!"
)


async def handle(interaction: discord.Interaction) -> None:
    if interaction.user.bot:
        return
    # ephemeral 응답이라 취침 게이트 없이 24시간 동작한다.
    await interaction.response.defer(ephemeral=True)
    await touch_channel(interaction)
    await interaction.edit_original_response(content=_COLLECTION_NOTICE)
