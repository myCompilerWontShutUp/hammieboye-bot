from dataclasses import dataclass
from typing import Awaitable, Callable, Union

import discord

CommandHandler = Callable[[int], Awaitable[Union[str, discord.Embed]]]

# 시스템 형태 메시지(호감도/대화 횟수 등)를 embed로 보일 때 쓰는 시그니처 컬러 (연주황색).
EMBED_COLOR = 0xFFCC99


def normalize(text: str) -> str:
    return text.replace(" ", "").strip().lower()


@dataclass(frozen=True)
class Command:
    name: str
    aliases: tuple[str, ...]
    handler: CommandHandler

    def matches(self, text: str) -> bool:
        target = normalize(text)
        return any(normalize(alias) == target for alias in self.aliases)
