from db.client import insert, select


async def record_purchase(user_id: int, item_id: str, price: int) -> None:
    """자판기 구매 1건을 기록한다(2026-09-08 신규) — 구매는 이제 항상 1개 단위라
    수량 컬럼이 필요 없다. 이 로그가 그대로 /자판기-리스트의 "(N회 구매)" 표시와
    동전 카테고리 가격 인상(2배씩) 계산의 근거가 된다."""
    await insert("vending_purchases", {"user_id": user_id, "item_id": item_id, "price": price})


async def count_purchases(user_id: int, item_id: str) -> int:
    """이 유저가 지금까지 이 품목을 몇 번 샀는지 — 동전 카테고리 품목의 다음 구매
    가격(base_price * 2^count)을 계산할 때 쓴다."""
    rows = await select(
        "vending_purchases",
        {"user_id": f"eq.{user_id}", "item_id": f"eq.{item_id}", "select": "id"},
    )
    return len(rows)


async def get_purchase_counts(user_id: int) -> dict[str, int]:
    """이 유저의 품목별 누적 구매 횟수 — /자판기-리스트가 카테고리 하나를 그릴 때
    한 번만 호출해서(품목마다 따로 조회하지 않고) 전부 채운다."""
    rows = await select("vending_purchases", {"user_id": f"eq.{user_id}", "select": "item_id"})
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["item_id"]] = counts.get(row["item_id"], 0) + 1
    return counts
