import discord
from discord import app_commands


def create_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    return discord.Client(intents=intents)


def create_tree(client: discord.Client) -> app_commands.CommandTree:
    return app_commands.CommandTree(client)
