from db.client import rpc


async def add_affection(user_id: int, amount: int, method: str | None = None) -> dict:
    """호감도를 원자적으로 증감시킨다 (일일 +20 상한은 DB 함수가 알아서 처리).

    amount는 양수(획득)/음수(하락) 둘 다 가능. 반환값은
    {applied_amount, new_affection, new_daily_gain}.
    """
    rows = await rpc(
        "add_affection",
        {"p_user_id": user_id, "p_amount": amount, "p_method": method},
    )
    return rows[0]


async def add_affection_uncapped(user_id: int, amount: int, method: str | None = None) -> int:
    """일일 +20 획득 상한 계산을 건너뛰고 무조건 적용한다 (예: 취침 중 깨움 이벤트의 악몽 감사 +5)."""
    rows = await rpc(
        "add_affection_uncapped",
        {"p_user_id": user_id, "p_amount": amount, "p_method": method},
    )
    return rows[0]["new_affection"]


async def apply_global_penalty(amount: int) -> None:
    """등록된 모든 사용자에게 한 번에 호감도를 증감시킨다 (3-2: 10분 무응답 시 전원 -1)."""
    await rpc("apply_global_penalty", {"p_amount": amount})


def format_affection_notice(delta: int, current: int) -> str:
    """호감도 변화를 하트 이모지와 함께 알려주는 문구. delta==0이면 호출하지 않는다."""
    emoji = "💕" if delta > 0 else "💔"
    sign = "+" if delta > 0 else ""
    return f"\n{emoji} 호감도 {sign}{delta} (현재 {current})"
