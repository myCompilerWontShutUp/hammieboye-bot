from db.client import delete, insert, select


async def record_purchase(user_id: int, item_id: str, price: int, count: int = 1) -> None:
    """자판기 구매를 기록한다(2026-09-08 신규) — 실제 구매(/자판기·/암시장)는 항상 1개
    단위라 count=1로만 불리지만, 관리자 콘솔 itm get은 한 번에 여러 개를 지급할 수 있어
    count>1이면 벌크 삽입으로 그만큼 행을 남긴다(수량 컬럼이 없어 "1행=1개"를 유지해야
    "(N회 구매)" 표시와 투자 카테고리 가격 인상(2배씩) 계산이 그대로 맞는다)."""
    if count <= 0:
        return
    rows = [{"user_id": user_id, "item_id": item_id, "price": price} for _ in range(count)]
    await insert("vending_purchases", rows)


async def remove_purchases(user_id: int, item_id: str, count: int) -> int:
    """이 유저의 이 품목 구매 기록을 최대 count개 지운다(관리자 itm remove 전용) —
    보유량보다 많이 지우려 하면 있는 만큼만 지우고 실제로 지운 개수를 반환한다."""
    if count <= 0:
        return 0
    rows = await select(
        "vending_purchases",
        {
            "user_id": f"eq.{user_id}",
            "item_id": f"eq.{item_id}",
            "select": "id",
            "order": "purchased_at.asc",
            "limit": str(count),
        },
    )
    if not rows:
        return 0
    ids = ",".join(str(row["id"]) for row in rows)
    await delete("vending_purchases", {"id": f"in.({ids})"})
    return len(rows)


async def clear_purchases(user_id: int) -> None:
    """이 유저의 구매 기록을 전부 지운다(관리자 itm clear 전용)."""
    await delete("vending_purchases", {"user_id": f"eq.{user_id}"})


async def count_purchases(user_id: int, item_id: str) -> int:
    """이 유저가 지금까지 이 품목을 몇 번 샀는지 — 동전 카테고리 품목의 다음 구매
    가격(base_price * 2^count)을 계산할 때 쓴다."""
    rows = await select(
        "vending_purchases",
        {"user_id": f"eq.{user_id}", "item_id": f"eq.{item_id}", "select": "id"},
    )
    return len(rows)


async def get_purchase_counts(user_id: int) -> dict[str, int]:
    """이 유저의 품목별 누적 구매 횟수 — /자판기·/암시장이 카테고리 하나를 그릴 때
    한 번만 호출해서(품목마다 따로 조회하지 않고) 전부 채운다."""
    rows = await select("vending_purchases", {"user_id": f"eq.{user_id}", "select": "item_id"})
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["item_id"]] = counts.get(row["item_id"], 0) + 1
    return counts
