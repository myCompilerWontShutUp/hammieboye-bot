import aiohttp

from config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

_BASE_URL = SUPABASE_URL.rstrip("/")
_HEADERS = {
    "apikey": SUPABASE_SERVICE_ROLE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    "Content-Type": "application/json",
}

_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(headers=_HEADERS)
    return _session


async def close() -> None:
    if _session is not None and not _session.closed:
        await _session.close()


async def select(table: str, params: dict[str, str]) -> list[dict]:
    session = _get_session()
    async with session.get(f"{_BASE_URL}/{table}", params=params) as resp:
        resp.raise_for_status()
        return await resp.json()


async def insert(table: str, data: dict | list[dict]) -> list[dict]:
    """data가 list[dict]면 PostgREST 벌크 삽입(한 요청에 여러 행) — 관리자 itm get처럼
    같은 값을 여러 행 남겨야 할 때 왕복 횟수를 줄이는 용도."""
    session = _get_session()
    headers = {"Prefer": "return=representation"}
    async with session.post(f"{_BASE_URL}/{table}", json=data, headers=headers) as resp:
        resp.raise_for_status()
        return await resp.json()


async def upsert(table: str, data: dict, *, on_conflict: str) -> list[dict]:
    session = _get_session()
    headers = {
        "Prefer": "return=representation,resolution=merge-duplicates",
    }
    params = {"on_conflict": on_conflict}
    async with session.post(
        f"{_BASE_URL}/{table}", params=params, json=data, headers=headers
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def update(table: str, params: dict[str, str], data: dict) -> list[dict]:
    session = _get_session()
    headers = {"Prefer": "return=representation"}
    async with session.patch(
        f"{_BASE_URL}/{table}", params=params, json=data, headers=headers
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def delete(table: str, params: dict[str, str]) -> list[dict]:
    session = _get_session()
    headers = {"Prefer": "return=representation"}
    async with session.delete(f"{_BASE_URL}/{table}", params=params, headers=headers) as resp:
        resp.raise_for_status()
        return await resp.json()


async def rpc(name: str, args: dict) -> object:
    session = _get_session()
    async with session.post(f"{_BASE_URL}/rpc/{name}", json=args) as resp:
        resp.raise_for_status()
        if resp.content_type == "application/json":
            return await resp.json()
        return await resp.text()
