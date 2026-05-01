"""FastAPI HTTP entrypoint for the Siri-driven expense bot."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from accountify_client import AccountifyClient
from agent import run_agent


load_dotenv()

BASE_URL = os.environ["ACCOUNTIFY_BASE_URL"]
USERNAME = os.environ["ACCOUNTIFY_USERNAME"]
PASSWORD = os.environ["ACCOUNTIFY_PASSWORD"]
ENTITY_ID = os.environ.get("ACCOUNTIFY_ENTITY_ID") or None
SHARED_SECRET = os.environ["BOT_SHARED_SECRET"]
HOST = os.environ.get("BOT_HOST", "127.0.0.1")
PORT = int(os.environ.get("BOT_PORT", "8787"))


class ExpenseIn(BaseModel):
    text: str


class ExpenseOut(BaseModel):
    reply: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = AccountifyClient(base_url=BASE_URL, username=USERNAME, password=PASSWORD)
    await client.__aenter__()
    app.state.client = client

    entity_id = ENTITY_ID
    if not entity_id:
        entities = await client.list_entities()
        if not entities:
            raise RuntimeError("No entities available for this user.")
        entity_id = entities[0]["id"]
        print(f"[bot] Using entity {entity_id} ({entities[0].get('name', '?')})")
    app.state.entity_id = entity_id

    try:
        yield
    finally:
        await client.__aexit__(None, None, None)


app = FastAPI(lifespan=lifespan)


def require_secret(x_bot_secret: str | None = Header(default=None)) -> None:
    if x_bot_secret != SHARED_SECRET:
        raise HTTPException(status_code=401, detail="bad secret")


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "entity_id": app.state.entity_id}


@app.post("/expense", response_model=ExpenseOut, dependencies=[Depends(require_secret)])
async def expense(body: ExpenseIn) -> ExpenseOut:
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="empty text")
    reply = await run_agent(body.text, app.state.client, app.state.entity_id)
    return ExpenseOut(reply=reply)


if __name__ == "__main__":
    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
