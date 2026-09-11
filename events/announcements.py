"""레벨업/업적 달성 글로벌 방송 + XP 적립 오케스트레이션(2026-09-10 신규, CLAUDE.md
§23) — 업적 달성 시 지급되던 호감도 보너스가 폐지되고 XP+글로벌 방송으로 바뀌면서,
레벨업 방송과 업적 방송이 로직을 공유한다(사용자 지시: "레벨업과 같은 로직으로
동작"). db/xp.py의 순수 RPC 래퍼를 감싸 "적립 → 레벨업 판정 → (레벨업이면) 레벨
보상 업적 부여 → 방송"까지 한 번에 처리하는 단일 진입점을 제공한다.

봇이 있는 모든 서버에 방송한다(events/scheduler.py::broadcast_to_guilds 재사용,
유저별로 실제 소속 서버만 골라 보내는 방식은 API 호출이 늘고 대량 레벨업 시
부담이 커서 채택하지 않음). 이름 표기는 core/discord_names.py::resolve_real_name
(실제/글로벌 이름, 멘션 안 함)을 쓴다 — 헬프미 이벤트가 다른 서버에 결과를 알릴 때
쓰는 것과 동일한 원칙(방송 하나가 여러 서버에 동시에 나가는 구조라 서버별로 다른
텍스트를 만들 수 없다)."""

import asyncio
import logging

import discord

import achievements
import levels
from config import ALLOWED_GUILD_IDS
from core.discord_names import resolve_real_name
from db.wallet import add_coins
from db.xp import add_xp, claim_affection_xp, claim_daily_base_xp, claim_nl_xp
from events.scheduler import broadcast_to_guilds

_client: discord.Client | None = None

# _handle_levelup()이 asyncio.create_task로 띄우는 레벨업 방송이 완료 전에 GC되지
# 않도록 강한 참조를 들고 있다가 끝나면 스스로 빠진다 — db/achievements.py
# ::_background_tasks와 동일한 표준 idiom(2026-09-11 추가).
_background_tasks: set[asyncio.Task] = set()


def _fire_and_forget(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

# 레벨업 축하 코인(2026-09-10 신규, CLAUDE.md §23) — "레벨업한 레벨 x 100"
# (사용자 지시). apply_day_multiplier=False — 업적 달성 보너스(舊)와 동일한 원칙으로
# 날짜 배율 미적용, 고정 수치 그대로 지급.
_LEVEL_UP_COIN_MULTIPLIER = 100


def init(client: discord.Client) -> None:
    global _client
    _client = client


async def _broadcast_background(coro, *, label: str) -> None:
    """fire-and-forget 방송 코루틴 공용 래퍼 — 아무도 이 태스크를 기다리지 않으므로
    예외를 여기서 직접 잡아 로그만 남긴다(db/achievements.py::_grant_xp_and_broadcast와
    동일한 원칙)."""
    try:
        await coro
    except Exception:
        logging.exception("Failed to send background broadcast (%s)", label)


async def broadcast_level_up(user_id: int, level: "levels.Level", *, guild_id: int | None = None) -> None:
    if _client is None:
        return
    name = await resolve_real_name(_client, user_id)
    coin_reward = level.number * _LEVEL_UP_COIN_MULTIPLIER
    text = (
        f"🎉 {name}님이 레벨 {level.number}({level.name})(으)로 레벨업했습니다!! "
        f"🪙 햄미가 축하 선물로 {coin_reward}코인을 가지고 왔습니다!"
    )
    await broadcast_to_guilds(_client, ALLOWED_GUILD_IDS, content=text, origin_guild_id=guild_id)


async def broadcast_level_up_batch(
    level_up_events: list[tuple[int, "levels.Level"]], *, guild_id: int | None = None
) -> None:
    """대량 XP 조작(관리자 콘솔 `exp up : *all` 등)으로 한 번에 여러 명이 동시에
    레벨업할 때 — 개별 방송 대신 서버당 요약 메시지 하나로 묶는다(레벨업 스팸 방지).
    level_up_events는 (user_id, 새로 도달한 Level) 순서쌍 리스트, 비어있으면
    아무것도 안 보낸다. guild_id는 이 명령을 실행한 관리자가 있던 서버(있으면
    그 서버를 먼저 보낸다)."""
    if _client is None or not level_up_events:
        return
    lines = []
    for user_id, level in level_up_events:
        name = await resolve_real_name(_client, user_id)
        coin_reward = level.number * _LEVEL_UP_COIN_MULTIPLIER
        lines.append(f"{name}님 → 레벨 {level.number}({level.name}) (🪙 +{coin_reward}코인)")
    text = "🎉 여러 명이 한꺼번에 레벨업했어요!!\n" + "\n".join(lines)
    await broadcast_to_guilds(_client, ALLOWED_GUILD_IDS, content=text, origin_guild_id=guild_id)


async def broadcast_achievement_unlock(user_id: int, achievement_module, *, guild_id: int | None = None) -> None:
    if _client is None:
        return
    name = await resolve_real_name(_client, user_id)
    text = f"🏆 {name}님이 '{achievements.format_name(achievement_module)}' 업적을 달성했습니다!!"
    await broadcast_to_guilds(_client, ALLOWED_GUILD_IDS, content=text, origin_guild_id=guild_id)


async def _handle_levelup(
    user_id: int, old_total: int, new_total: int, *, broadcast: bool, guild_id: int | None = None
) -> "levels.Level | None":
    """old_total과 new_total 사이에 레벨 경계를 넘었으면 (필요 시 레벨 보상 업적을
    부여하고) 새 Level을 반환, 안 넘었으면 None. broadcast=False면 방송은 생략하고
    새 Level만 반환한다(호출부가 배치로 모아서 broadcast_level_up_batch로 직접
    처리 — 대량 조작 시나리오 전용). guild_id는 이 XP 적립을 유발한 활동이 있었던
    서버 — 알고 있으면 그 서버가 전 서버 방송에서 가장 먼저 보인다(2026-09-11).

    **레벨업 방송 자체는 진짜 fire-and-forget이다**(2026-09-11 추가 수정) —
    db/achievements.py::award()와 동일한 이유: 이 함수(및 이를 호출하는
    grant_affection_xp/grant_nl_xp 등)가 command/slot.py::_spin_row처럼 아직
    interaction.response를 한 번도 안 부른 버튼 콜백 한복판에서 그대로 `await`
    당하고 있었는데, 봇이 있는 모든 서버에 순회 방송하는 이 호출이 느려지면
    Discord의 3초 응답 제한을 넘겨 "Unknown interaction" 404가 나는, award()에서
    이미 한 번 고쳤던 것과 완전히 같은 종류의 사고가 재현될 수 있었다. 업적 부여
    (award_achievement)와 코인 지급(add_coins)은 그대로 동기 처리한다 — 둘 다
    단일 DB 왕복이라 빠르고, 상태가 실제로 반영됐는지가 바로 다음 로직(중복 방지 등)에
    영향을 줄 수 있어 백그라운드로 미루면 오히려 추적이 복잡해진다. 오직 실제로
    네트워크 호출이 여러 번(서버 수만큼) 나가는 방송 부분만 떼어낸다."""
    old_level = levels.get_level_for_xp(old_total)
    new_level = levels.get_level_for_xp(new_total)
    if new_level.number <= old_level.number:
        return None

    if new_level.achievement_id is not None:
        # db.achievements가 award() 내부에서 이 모듈을 다시 참조하므로(업적 달성
        # 글로벌 방송) 지역 import로 순환을 끊는다 — db/achievements.py의 기존
        # add_affection_uncapped 지역 import와 동일한 원칙.
        from db.achievements import award as award_achievement

        await award_achievement(user_id, new_level.achievement_id, guild_id=guild_id)

    await add_coins(
        user_id,
        new_level.number * _LEVEL_UP_COIN_MULTIPLIER,
        method="level_up_bonus",
        count_as_earned=True,
        guild_id=guild_id,
    )

    if broadcast:
        _fire_and_forget(
            _broadcast_background(broadcast_level_up(user_id, new_level, guild_id=guild_id), label="level_up")
        )
    return new_level


async def apply_xp_and_check_levelup(
    user_id: int, amount: int, *, broadcast: bool = True, guild_id: int | None = None
) -> "levels.Level | None":
    """무조건 누적(add_xp, 일일 상한 없음) + 레벨업 판정을 한 번에 처리하는 단일
    진입점 — 업적(db/achievements.py::award)·헬프미 이벤트·디저트 타임·관리자
    `exp` 명령어가 공유한다. guild_id는 방송 시 우선 전송할 서버(알 수 있으면).

    add_xp가 갱신 전/후 total_xp를 한 번에 돌려주므로(2026-09-11) 레벨업 판정을
    위해 별도로 get_user()를 먼저 부를 필요가 없다."""
    old_total, new_total = await add_xp(user_id, amount)
    return await _handle_levelup(user_id, old_total, new_total, broadcast=broadcast, guild_id=guild_id)


async def grant_daily_base_xp(user_id: int, *, guild_id: int | None = None) -> None:
    """그날 첫 자연어 대화 시 1회만 +1 — core/chat.py에서만 호출한다. 2026-09-11부로
    슬래시 명령어는 어떤 경로로도 XP를 안 주므로(core/slash_commands.py::_prepare()가
    이 함수를 더 이상 호출하지 않음), "그날 첫 활동"의 기준이 자연어 대화로
    좁혀졌다."""
    applied, new_total = await claim_daily_base_xp(user_id)
    if applied > 0:
        await _handle_levelup(user_id, new_total - applied, new_total, broadcast=True, guild_id=guild_id)


async def grant_affection_xp(user_id: int, applied_amount: int, *, guild_id: int | None = None) -> None:
    """호감도가 실제로 오른 만큼(applied_amount, 음수/0은 무시)을 2배로 환산해
    지급한다 — 하루 200xp 상한은 claim_affection_xp(db/xp.py)가 원자적으로
    처리한다. db/affection.py::add_affection()/add_affection_uncapped() 양쪽
    성공 경로 끝에서 호출(관리자 fl 조작, 암시장 확률형 간식 uncapped 경로도
    포함 — 호감도가 바뀌는 모든 경로를 놓치지 않기 위함)."""
    if applied_amount <= 0:
        return
    applied, new_total = await claim_affection_xp(user_id, applied_amount * 2)
    if applied > 0:
        await _handle_levelup(user_id, new_total - applied, new_total, broadcast=True, guild_id=guild_id)


async def grant_nl_xp(user_id: int, *, guild_id: int | None = None) -> None:
    """자연어 대화 1번 XP(하루 5회 상한) — 실제로 생성까지 도달한 메시지에서만
    호출(over_cap/반복 페널티/이벤트 오버라이드 경로는 호출 안 함)."""
    applied, new_total = await claim_nl_xp(user_id)
    if applied > 0:
        await _handle_levelup(user_id, new_total - applied, new_total, broadcast=True, guild_id=guild_id)
