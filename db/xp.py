from db.client import rpc


async def add_xp(user_id: int, amount: int) -> tuple[int, int]:
    """무조건 누적(업적/헬프미 이벤트/디저트 타임/관리자 exp 명령어 전용, 일일 상한
    없음 — 업적은 1회성이라 파밍 불가, 이벤트는 하루 최대 발생 횟수가 이미 있어
    추가 상한이 불필요). (갱신 전 total_xp, 갱신 후 total_xp)를 반환한다 — RPC가
    같은 트랜잭션 안에서 FOR UPDATE로 갱신 전 값도 같이 읽어 돌려주므로,
    events/announcements.py::apply_xp_and_check_levelup이 레벨업 판정을 위해
    갱신 전 값을 알려고 별도 get_user() 왕복을 할 필요가 없다(2026-09-11 수정)."""
    rows = await rpc("add_xp", {"p_user_id": user_id, "p_amount": amount})
    row = rows[0]
    return row["old_total"], row["new_total"]


async def _claim_and_fetch_total(rpc_name: str, args: dict) -> tuple[int, int]:
    """claim_* RPC 공통 후처리 — RPC 자체가 (applied, new_total)을 한 번에 돌려준다
    (2026-09-11 변경 — 舊에는 RPC가 applied만 반환해서, applied>0일 때마다 새
    total_xp를 알려고 별도 get_user() 왕복을 한 번 더 했다. claim_nl_xp가 거의
    매 자연어 메시지마다 이 경로를 타서 이 왕복 하나가 체감 지연의 큰 부분이었다 —
    RPC가 `UPDATE ... RETURNING total_xp`로 이미 들고 있는 값을 그대로 같이
    돌려주면 이 왕복 자체가 사라진다). applied=0(그날 상한 소진)이면 new_total은
    NULL로 오고, 호출부는 이 값을 안 쓰므로 그대로 0으로 정규화해 반환한다."""
    rows = await rpc(rpc_name, args)
    row = rows[0]
    applied = row["applied"]
    if applied <= 0:
        return applied, 0
    return applied, row["new_total"]


async def claim_affection_xp(user_id: int, raw_amount: int) -> tuple[int, int]:
    """호감도→XP 전환(호감도 +1당 XP +2는 호출부가 이미 2배로 계산해서 넘긴다,
    하루 200 상한은 이 RPC가 원자적으로 처리). (실제 적용량, 적용 후 total_xp)."""
    return await _claim_and_fetch_total(
        "claim_affection_xp", {"p_user_id": user_id, "p_raw_amount": raw_amount}
    )


async def claim_nl_xp(user_id: int) -> tuple[int, int]:
    """자연어 대화 1번 XP(하루 5회 상한)."""
    return await _claim_and_fetch_total("claim_nl_xp", {"p_user_id": user_id})


async def claim_daily_base_xp(user_id: int) -> tuple[int, int]:
    """그날 첫 활동(자연어 또는 슬래시 커맨드) 시 1회만 지급되는 기본 XP."""
    return await _claim_and_fetch_total("claim_daily_base_xp", {"p_user_id": user_id})
