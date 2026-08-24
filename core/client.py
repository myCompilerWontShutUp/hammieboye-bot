import discord


def create_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    return discord.Client(intents=intents)
