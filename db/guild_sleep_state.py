from datetime import datetime, timedelta, timezone

from db.client import rpc, select

_KST_OFFSET = timedelta(hours=9)


def _kst_today_str() -> str:
    return (datetime.now(timezone.utc) + _KST_OFFSET).date().isoformat()


async def register_mention(guild_id: int) -> bool:
    """맨션 1회를 원자적으로 기록한다.

    밤이 바뀌었으면 서버 임계치를 새로 뽑아 리셋하고, 이번 호출로 막 임계치에
    도달해 깨움 이벤트가 발동했으면 True를 반환한다(그 서버는 이후 같은 밤
    동안 다시 발동하지 않는다). DB 함수(register_sleep_mention)가 행 잠금으로
    직렬화하므로, 같은 서버에서 서로 다른 유저가 동시에 맨션해도 안전하다.
    """
    rows = await rpc("register_sleep_mention", {"p_guild_id": guild_id})
    return rows[0]["just_triggered"]


async def any_triggered_tonight() -> bool:
    """오늘(KST) 밤 어느 서버에서든 방해금지 이벤트가 이미 발동했는지.

    발동 여부(`triggered`)는 DB에 남아 재시작에도 그대로 유지되지만, presence(상태 표시)와
    §28의 지연 기상 플래그는 봇 프로세스 메모리에만 있어서 재배포/재시작하면 사라진다 —
    `on_ready()`가 이 함수로 DB 기준 진실을 확인해서 그 둘을 복원하는 데 쓴다.
    """
    rows = await select(
        "guild_sleep_state",
        {"sleep_date": f"eq.{_kst_today_str()}", "triggered": "eq.true", "select": "guild_id", "limit": "1"},
    )
    return bool(rows)
