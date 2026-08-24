from datetime import datetime, timezone

from db.client import insert, select


async def log(user_id: int, guild_id: int, content: str, role: str = "user") -> None:
    await insert(
        "chat_history",
        {"user_id": user_id, "guild_id": guild_id, "content": content, "role": role},
    )


async def get_recent(user_id: int, since: datetime, role: str = "user") -> list[dict]:
    """최근 히스토리를 최신순으로 반환한다 (CLAUDE.md 1-2: 30분/최대 50개 범위 내에서 호출할 것).

    기본값 role="user"는 4-1/4-2 판정과 "최근 대화" 표시가 유저 본인의 발화만
    보도록 기존 동작을 그대로 유지하기 위함이다. 햄미 자신의 답장은 role="assistant"로
    별도 저장되며 get_recent_turns()로 조회한다.
    """
    rows = await select(
        "chat_history",
        {
            "user_id": f"eq.{user_id}",
            "created_at": f"gte.{since.astimezone(timezone.utc).isoformat()}",
            "role": f"eq.{role}",
            "select": "content,created_at",
            "order": "created_at.desc",
            "limit": "50",
        },
    )
    return rows


async def get_recent_turns(user_id: int, since: datetime, limit: int = 5) -> list[dict]:
    """최근 대화 턴(유저 발화 + 햄미 답장 둘 다)을 오래된 순으로 최대 limit개 반환한다.

    자연어 생성 시 직전 맥락을 모델 입력에 같이 넣어주기 위한 용도라, role/content가
    OpenAI Responses API의 input 메시지 형식과 그대로 호환된다.
    """
    rows = await select(
        "chat_history",
        {
            "user_id": f"eq.{user_id}",
            "created_at": f"gte.{since.astimezone(timezone.utc).isoformat()}",
            "select": "role,content,created_at",
            "order": "created_at.desc",
            "limit": str(limit),
        },
    )
    return list(reversed(rows))
