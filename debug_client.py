"""Bypass the agent — call the client directly to see raw API responses."""
import asyncio
import os
import json
from dotenv import load_dotenv
from accountify_client import AccountifyClient


async def main():
    load_dotenv()
    async with AccountifyClient(
        base_url=os.environ["ACCOUNTIFY_BASE_URL"],
        username=os.environ["ACCOUNTIFY_USERNAME"],
        password=os.environ["ACCOUNTIFY_PASSWORD"],
    ) as client:
        entity_id = os.environ.get("ACCOUNTIFY_ENTITY_ID") or None
        if not entity_id:
            entities = await client.list_entities()
            entity_id = entities[0]["id"]
            print(f"entity: {entity_id}")
        accounts = await client.list_accounts(entity_id)
        print(f"got {len(accounts)} accounts\n")
        by_type: dict[str, list] = {}
        for a in accounts:
            by_type.setdefault(a.get("account_type", "?"), []).append(a)
        for atype in ("asset", "liability", "equity", "revenue", "expense"):
            rows = by_type.get(atype, [])
            if not rows:
                continue
            print(f"--- {atype.upper()} ({len(rows)}) ---")
            for a in rows:
                print(f"  {a.get('code',''):<6} {a.get('name',''):<35} {a.get('default_currency','')}  active={a.get('is_active', True)}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
