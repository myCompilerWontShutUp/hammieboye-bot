from datetime import datetime, timezone

from db.client import insert, select, update


async def log(user_id: int, guild_id: int, content: str, role: str = "user") -> dict:
    """chat_history에 한 줄 남기고, 그 행을 반환한다 — 자연어 메시지는 감정 판정이 로그를
    남긴 "이후"에 나오므로(핸디캡: 판정 전에 이미 로그부터 남겨야 4-1/4-2 판정이 정확함),
    호출부가 반환된 id로 set_detected_emotion()을 나중에 걸 수 있어야 한다."""
    rows = await insert(
        "chat_history",
        {"user_id": user_id, "guild_id": guild_id, "content": content, "role": role},
    )
    return rows[0]


async def set_detected_emotion(row_id: int, emotion: str) -> None:
    """분류가 끝난 뒤, 이미 남겨둔 chat_history 행에 판정된 감정을 채워 넣는다."""
    await update("chat_history", {"id": f"eq.{row_id}"}, {"detected_emotion": emotion})


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
