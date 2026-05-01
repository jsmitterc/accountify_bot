"""Direct agent test — no HTTP, no Siri. Run after `pip install -r requirements.txt`.

Usage:
    python smoke_test.py "add 50.18 aud to my supermarket expense paid with my commonwealth account"
"""
from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

from accountify_client import AccountifyClient
from agent import run_agent


async def main(utterance: str) -> None:
    load_dotenv()
    base_url = os.environ["ACCOUNTIFY_BASE_URL"]
    username = os.environ["ACCOUNTIFY_USERNAME"]
    password = os.environ["ACCOUNTIFY_PASSWORD"]
    entity_id = os.environ.get("ACCOUNTIFY_ENTITY_ID") or None

    async with AccountifyClient(base_url=base_url, username=username, password=password) as client:
        if not entity_id:
            entities = await client.list_entities()
            entity_id = entities[0]["id"]
            print(f"[smoke] using entity {entity_id} ({entities[0].get('name', '?')})")
        reply = await run_agent(utterance, client, entity_id)
        print("\n=== Agent reply ===")
        print(reply)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(" ".join(sys.argv[1:])))
