import discord
from discord import app_commands


def create_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    # /소개(core/intro.py)가 서버 전체 멤버를 별명으로 검색해야 해서 필요하다. 이건
    # privileged intent라 코드에서 켜는 것만으로는 부족하고, Discord 개발자 포털의 봇 설정에서
    # "Server Members Intent"도 별도로 켜야 한다 — 안 켜면 로그인 자체가 실패한다.
    intents.members = True
    return discord.Client(intents=intents)


def create_tree(client: discord.Client) -> app_commands.CommandTree:
    return app_commands.CommandTree(client)
