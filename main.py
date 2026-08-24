import asyncio
import logging

import db.client
from config import DISCORD_TOKEN
from core.client import create_client
from core.dispatcher import setup_dispatcher

logging.basicConfig(level=logging.INFO)

client = create_client()
setup_dispatcher(client)


async def main() -> None:
    try:
        async with client:
            await client.start(DISCORD_TOKEN)
    finally:
        await db.client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Shutting down.")
