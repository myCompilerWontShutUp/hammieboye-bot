from db.client import rpc, select


async def get_inventory(user_id: int) -> list[dict]:
    """0개보다 많이 가진 간식만 반환한다({snack_id, quantity} 목록, 순서는 미보장 —
    호출부가 카탈로그 순서로 다시 정렬해야 하면 command/vending_catalog.py를 참고)."""
    return await select(
        "user_snacks",
        {"user_id": f"eq.{user_id}", "quantity": "gt.0", "select": "snack_id,quantity"},
    )


async def add_snack(user_id: int, snack_id: str, quantity: int) -> int:
    """간식을 지급한다(없으면 새로 만들고, 있으면 누적) — 자판기 구매 전용. 반환값은
    그 간식의 갱신 후 총 개수."""
    return await rpc(
        "add_snack",
        {"p_user_id": user_id, "p_snack_id": snack_id, "p_quantity": quantity},
    )


async def consume_snack(user_id: int, snack_id: str) -> bool:
    """간식 1개를 원자적으로 소비한다(조건부 차감, spend_coins와 동일한 idiom) —
    /먹어 전용. 실패(False)면 그 간식을 안 가지고 있거나 0개인 것."""
    return await rpc("consume_snack", {"p_user_id": user_id, "p_snack_id": snack_id})
