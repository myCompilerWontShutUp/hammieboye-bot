import logging

import discord

# 사용자를 실제 멘션(<@id>)하지 않는다 — 다른 서버에 잘못된 핑으로 뜨거나 안 보일 수
# 있어서다. 서버 별명(display_name)도 아닌 "실제 이름"(global_name, 없으면 name)을 쓴다.


async def resolve_real_name(client: discord.Client, user_id: int) -> str:
    """서버 별명이 아닌 실제(글로벌) 이름을 구한다. 캐시에 없으면 API로 조회하고,
    그마저 실패하면 최후의 수단으로 ID를 텍스트에 남긴다."""
    user = client.get_user(user_id)
    if user is None:
        try:
            user = await client.fetch_user(user_id)
        except discord.HTTPException:
            logging.exception("Failed to resolve real name for user %s", user_id)
            return f"어떤 친구({user_id})"
    return user.global_name or user.name
