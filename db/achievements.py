import asyncio
import logging

import aiohttp

import achievements
from db.client import delete, insert, select

# award()가 asyncio.create_task로 띄우는 배경 작업(XP 지급/레벨업 판정/글로벌 방송)이
# 완료 전에 가비지 컬렉션되지 않도록 강한 참조를 들고 있다가 끝나면 스스로 빠진다
# ("Task was destroyed but it is pending" 경고를 막는 표준 asyncio 패턴).
_background_tasks: set[asyncio.Task] = set()


def _fire_and_forget(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def has_earned(user_id: int, achievement_id: str) -> bool:
    rows = await select(
        "user_achievements",
        {"user_id": f"eq.{user_id}", "achievement_id": f"eq.{achievement_id}", "select": "user_id"},
    )
    return bool(rows)


_LEGENDARY_XP = 30
_NORMAL_XP = 5


async def award(user_id: int, achievement_id: str, *, guild_id: int | None = None) -> dict:
    """업적을 부여한다. 2026-09-10부로 호감도 보너스는 폐지됐다(기존에 이미 지급된
    호감도는 회수하지 않지만, 새 획득분부터는 호감도가 전혀 안 오른다) — 대신 XP를
    등급별로 지급(일반 +5/전설 +30, 업적은 1회성이라 파밍 위험이 없어 일일 상한
    없음)하고, 개인 응답에 인라인으로 붙던 알림 대신 전 서버 글로벌 방송으로
    알린다(레벨업과 동일한 로직 공유, events/announcements.py).

    반환값은 {earned} 하나뿐이다(舊 applied_amount/new_affection은 더 이상 의미가
    없어 제거 — 호출부는 이제 반환값의 텍스트를 조립하지 않고, earned 여부만 필요하면
    확인한다). earned=False면 이미 가지고 있던 상태. has_earned() 확인과 insert()는
    원자적이지 않다 — 거의 동시 요청 두 개가 둘 다 has_earned()==False로 통과하면
    insert() 하나만 성공하고 나머지는 PK 충돌로 409를 받을 수 있다. 이걸 못 잡으면
    예외가 새서 메시지 처리 전체가 죽으므로, 409는 "누군가 먼저 기록함"과 동일하게
    취급해 조용히 처리한다.

    XP 지급/레벨업 판정/글로벌 방송은 **진짜** fire-and-forget이다 — `await`로
    끝나기를 기다리지 않고 `asyncio.create_task`로 완전히 떼어낸다(2026-09-11 발견
    — 예전엔 이 무거운 파이프라인(여러 DB 왕복 + 봇이 있는 모든 서버에 순회
    방송)을 여기서 그대로 `await`했는데, 호출부가 `/자판기` 구매 확인 모달 응답이나
    더블오어낫띵 "여기까지" 정산처럼 Discord 인터랙션에 직접 응답해야 하는 경로
    한복판에서 이걸 기다리다가 Discord의 응답 제한(3초/토큰 만료)을 넘겨버려
    "Unknown interaction" 404로 정작 보여줘야 할 응답 자체가 실패하는 사고가
    실제로 있었다. `earned` 판정에 필요한 부분(has_earned/insert)만 여기서
    동기적으로 끝내고, 그 결과와 무관한 부수 효과는 백그라운드로 넘긴다.

    guild_id는 이 업적을 획득하게 만든 행동이 있었던 서버 — 알고 있으면 전 서버
    방송에서 그 서버를 가장 먼저 보낸다(2026-09-11, events/scheduler.py
    ::broadcast_to_guilds의 origin_guild_id 참고)."""
    if await has_earned(user_id, achievement_id):
        return {"earned": False}
    try:
        await insert("user_achievements", {"user_id": user_id, "achievement_id": achievement_id})
    except aiohttp.ClientResponseError as e:
        if e.status == 409:
            return {"earned": False}
        raise

    _fire_and_forget(_grant_xp_and_broadcast(user_id, achievement_id, guild_id))
    return {"earned": True}


async def _grant_xp_and_broadcast(user_id: int, achievement_id: str, guild_id: int | None) -> None:
    """award()가 배경 태스크로 띄우는 부수 효과 — 업적 자체는 이미 DB에 기록된
    뒤이므로, 여기서 예외가 나도(예: 레벨업 코인 지급 실패) 로그만 남기고 삼킨다
    — 애초에 이 함수를 기다리는 호출부가 아무도 없어(fire-and-forget) 예외를
    잡아줄 곳이 여기뿐이다."""
    # events/announcements.py가 레벨 보상 업적을 부여할 때 이 award()를 다시 호출하므로,
    # 여기서 최상단에 events.announcements를 import하면 순환된다 — 지역 import로 끊는다
    # (舊 add_affection_uncapped 지역 import와 동일한 원칙).
    from events.announcements import apply_xp_and_check_levelup, broadcast_achievement_unlock

    module = achievements.REGISTRY[achievement_id]
    bonus = _LEGENDARY_XP if module.RARITY == achievements.LEGENDARY else _NORMAL_XP
    try:
        await apply_xp_and_check_levelup(user_id, bonus, guild_id=guild_id)
        await broadcast_achievement_unlock(user_id, module, guild_id=guild_id)
    except Exception:
        logging.exception(
            "Failed to grant XP/broadcast for achievement %r (user %s) — the achievement "
            "itself was still recorded",
            achievement_id,
            user_id,
        )


async def revoke(user_id: int, achievement_id: str) -> bool:
    """관리자 콘솔의 ac revoke 전용. 가지고 있던 업적이면 지우고 True, 애초에 없었으면
    아무 것도 안 하고 False(중복 처리 없이 조용히 성공으로 취급하지 않고, 호출부가
    "원래 안 가지고 있었다"는 걸 구분할 수 있게 한다)."""
    if not await has_earned(user_id, achievement_id):
        return False
    await delete(
        "user_achievements",
        {"user_id": f"eq.{user_id}", "achievement_id": f"eq.{achievement_id}"},
    )
    return True


async def get_earned(user_id: int) -> list[dict]:
    """획득 순(오래된 순)으로 반환한다."""
    return await select(
        "user_achievements",
        {"user_id": f"eq.{user_id}", "select": "achievement_id,earned_at", "order": "earned_at.asc"},
    )


# 실제 상승분이 있으면 "햄미 러브 유"(최초 1회), 절대값 기준으로 "최고의 햄미 주인"(25
# 이상)/"아몬드 양보 가능"(250 이상, 전설). "이상"이라 계속 조건을 만족해도 award()가
# 중복을 막아준다.
_GREAT_OWNER_THRESHOLD = 25
_ALMOND_WORTHY_THRESHOLD = 250


async def maybe_award_affection_milestones(
    user_id: int, applied_amount: int, new_affection: int, *, guild_id: int | None = None
) -> None:
    """호감도가 바뀔 때마다 확인해야 하는 업적들을 한곳에서 처리한다. add_affection/
    add_affection_uncapped 안에서 호출되는 공용 훅 — 호감도가 바뀌는 경로가 여러 군데라
    개별 호출부마다 챙기지 않도록 중앙화했다.

    2026-09-10부로 업적 달성 알림은 award() 내부에서 글로벌 방송으로 처리되므로
    (호감도 보너스도 폐지) 이 함수는 더 이상 알림 문자열을 반환하지 않는다 — 호출부
    (db/affection.py::add_affection 등)가 여전히 result["achievement_notice"]에
    이 반환값을 그대로 대입하지만, 그 값이 항상 None이라 기존 "if achievement_notice:"
    분기들이 자연히 아무 것도 안 하게 된다(호출부 코드를 일일이 안 고쳐도 됨).

    la set/la reset(set_affection RPC)은 daily_stats/affection_log를 안 건드리는 별도
    경로라 이 훅을 거치지 않는다 — 그 경로로 마일스톤을 넘긴 경우는 ach grant가 우회로다.

    guild_id는 award()로 그대로 전달돼 전 서버 방송 시 우선 전송 서버로 쓰인다.

    세 조건은 서로 다른 업적을 확인하는 완전히 독립적인 검사라(공유 상태 없음,
    award() 자체가 각자 멱등하게 처리) 순차로 기다릴 이유가 없다 — asyncio.gather로
    동시에 보낸다(2026-09-11, 버튼 클릭 한복판에서 add_affection이 호출되는 경로
    — 예: command/slot.py 햄스터 페널티 — 의 체감 지연을 줄이기 위한 추가 조치).
    """
    checks = []
    if applied_amount > 0:
        checks.append(award(user_id, achievements.hammie_love_you.ID, guild_id=guild_id))
    if new_affection >= _GREAT_OWNER_THRESHOLD:
        checks.append(award(user_id, achievements.great_owner.ID, guild_id=guild_id))
    if new_affection >= _ALMOND_WORTHY_THRESHOLD:
        checks.append(award(user_id, achievements.almond_worthy.ID, guild_id=guild_id))
    if checks:
        await asyncio.gather(*checks)
