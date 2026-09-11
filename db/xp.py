from db.client import rpc
from db.users import get_user


async def add_xp(user_id: int, amount: int) -> int:
    """무조건 누적(업적/헬프미 이벤트/디저트 타임/관리자 exp 명령어 전용, 일일 상한
    없음 — 업적은 1회성이라 파밍 불가, 이벤트는 하루 최대 발생 횟수가 이미 있어
    추가 상한이 불필요). 새 total_xp를 반환한다."""
    return await rpc("add_xp", {"p_user_id": user_id, "p_amount": amount})


async def _claim_and_fetch_total(rpc_name: str, args: dict, user_id: int) -> tuple[int, int]:
    """claim_* RPC 공통 후처리 — applied(실제 적용량)가 0보다 크면 최신 total_xp를
    다시 조회해 (applied, new_total_xp)로 반환한다. applied=0(그날 상한 소진)이면
    total_xp가 안 바뀌었으니 재조회 없이 (0, 0)을 반환 — 호출부는 applied>0일 때만
    new_total_xp를 쓴다."""
    applied = await rpc(rpc_name, args)
    if applied <= 0:
        return applied, 0
    user = await get_user(user_id)
    return applied, user["total_xp"]


async def claim_affection_xp(user_id: int, raw_amount: int) -> tuple[int, int]:
    """호감도→XP 전환(호감도 +1당 XP +2는 호출부가 이미 2배로 계산해서 넘긴다,
    하루 200 상한은 이 RPC가 원자적으로 처리). (실제 적용량, 적용 후 total_xp)."""
    return await _claim_and_fetch_total(
        "claim_affection_xp", {"p_user_id": user_id, "p_raw_amount": raw_amount}, user_id
    )


async def claim_nl_xp(user_id: int) -> tuple[int, int]:
    """자연어 대화 1번 XP(하루 5회 상한)."""
    return await _claim_and_fetch_total("claim_nl_xp", {"p_user_id": user_id}, user_id)


async def claim_daily_base_xp(user_id: int) -> tuple[int, int]:
    """그날 첫 활동(자연어 또는 슬래시 커맨드) 시 1회만 지급되는 기본 XP."""
    return await _claim_and_fetch_total("claim_daily_base_xp", {"p_user_id": user_id}, user_id)
