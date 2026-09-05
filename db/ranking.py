import asyncio

from db.client import select

_CANDIDATE_POOL = 20


async def get_top_candidates() -> list[dict]:
    """호감도 상위 후보(여유 있게 넉넉히)를 가져온다. 동점 처리는 애플리케이션에서 한다."""
    return await select(
        "users",
        {
            "select": "user_id,affection,created_at",
            "order": "affection.desc",
            "limit": str(_CANDIDATE_POOL),
        },
    )


async def get_top_coin_candidates() -> list[dict]:
    """동전 보유량 상위 후보(여유 있게 넉넉히)를 가져온다 — /랭킹(동전 카테고리) 전용.
    get_top_candidates와 동일한 골격, 대상 컬럼만 coins로 바뀐다."""
    return await select(
        "users",
        {
            "select": "user_id,coins,created_at",
            "order": "coins.desc",
            "limit": str(_CANDIDATE_POOL),
        },
    )


async def get_last_increase_time(user_id: int, current_affection: int) -> str | None:
    """현재 호감도 값에 '증가로' 도달한 가장 최근 시각. 감소로 도달했다면 None."""
    rows = await select(
        "affection_log",
        {
            "user_id": f"eq.{user_id}",
            "new_value": f"eq.{current_affection}",
            "delta": "gt.0",
            "select": "created_at",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    return rows[0]["created_at"] if rows else None


def _member_filter(member_ids: list[int] | None) -> dict[str, str] | None:
    """member_ids가 None이면 전체(글로벌) 기준, 빈 리스트/일부 리스트면 그 안에서만(서버 기준)
    필터링하는 PostgREST 파라미터를 만든다. 빈 리스트는 애초에 쿼리를 보낼 필요가 없다는 뜻으로
    호출부에서 따로 처리한다."""
    if member_ids is None:
        return None
    return {"user_id": f"in.({','.join(str(m) for m in member_ids)})"}


async def _count_higher(affection: int, member_ids: list[int] | None) -> int:
    if member_ids is not None and not member_ids:
        return 0
    params = {"affection": f"gt.{affection}", "select": "user_id"}
    filter_params = _member_filter(member_ids)
    if filter_params:
        params.update(filter_params)
    rows = await select("users", params)
    return len(rows)


async def _get_tied(affection: int, member_ids: list[int] | None) -> list[dict]:
    if member_ids is not None and not member_ids:
        return []
    params = {"affection": f"eq.{affection}", "select": "user_id,created_at"}
    filter_params = _member_filter(member_ids)
    if filter_params:
        params.update(filter_params)
    return await select("users", params)


async def get_rank(user_id: int, affection: int, member_ids: list[int] | None = None) -> int:
    """affection 기준 순위(1부터 시작)를 계산한다. member_ids를 주면 그 안에서만(서버 랭크),
    생략하면(None) 전체 유저 기준(글로벌 랭크)으로 계산한다. 동점 처리는 /랭킹(호감도 카테고리)와 동일한 기준
    (증가로 먼저 도달한 사람 -> 가입일이 이른 사람 -> user_id가 낮은 사람)을 재사용한다."""
    higher, tied = await asyncio.gather(
        _count_higher(affection, member_ids),
        _get_tied(affection, member_ids),
    )
    if len(tied) <= 1:
        return higher + 1

    async def sort_key(row: dict) -> tuple:
        timestamp = await get_last_increase_time(row["user_id"], affection)
        return (0 if timestamp is not None else 1, timestamp or "", row["created_at"], row["user_id"])

    keyed = [((await sort_key(row)), row["user_id"]) for row in tied]
    keyed.sort(key=lambda pair: pair[0])
    position = next(i for i, (_, uid) in enumerate(keyed) if uid == user_id) + 1
    return higher + position


async def _count_higher_coins(coins: int, member_ids: list[int] | None) -> int:
    if member_ids is not None and not member_ids:
        return 0
    params = {"coins": f"gt.{coins}", "select": "user_id"}
    filter_params = _member_filter(member_ids)
    if filter_params:
        params.update(filter_params)
    rows = await select("users", params)
    return len(rows)


async def _get_tied_coins(coins: int, member_ids: list[int] | None) -> list[dict]:
    if member_ids is not None and not member_ids:
        return []
    params = {"coins": f"eq.{coins}", "select": "user_id,created_at"}
    filter_params = _member_filter(member_ids)
    if filter_params:
        params.update(filter_params)
    return await select("users", params)


async def get_coin_rank(user_id: int, coins: int, member_ids: list[int] | None = None) -> int:
    """coins 기준 순위(1부터 시작) — get_rank와 동일한 골격이지만, 동전은 "먼저 그
    값에 도달한 사람"을 판정할 이력 테이블(affection_log에 대응하는 것)이 없어서
    동점 타이브레이크가 (1) 가입일이 이른 사람 (2) user_id가 낮은 사람 2단계뿐이다
    (/랭킹(동전 카테고리)의 _coin_sort_key와 동일한 원칙)."""
    higher, tied = await asyncio.gather(
        _count_higher_coins(coins, member_ids),
        _get_tied_coins(coins, member_ids),
    )
    if len(tied) <= 1:
        return higher + 1

    keyed = sorted(tied, key=lambda row: (row["created_at"], row["user_id"]))
    position = next(i for i, row in enumerate(keyed) if row["user_id"] == user_id) + 1
    return higher + position


async def count_total(member_ids: list[int] | None = None) -> int:
    """순위 계산 모집단의 전체 인원 수. member_ids를 주면 서버 인원, 생략하면 전체 유저."""
    if member_ids is not None and not member_ids:
        return 0
    params: dict[str, str] = {"select": "user_id"}
    filter_params = _member_filter(member_ids)
    if filter_params:
        params.update(filter_params)
    rows = await select("users", params)
    return len(rows)


def compute_percentile(rank: int, total: int) -> int:
    """순위를 "상위 N%"로 변환한다. 1위는 무조건 1%, 꼴등은 무조건 99%(범위 1~99% 고정,
    0%/100% 없음), 소수점 버림, 인원 1명이면 1%."""
    if total <= 1:
        return 1
    return ((rank - 1) * 98) // (total - 1) + 1
