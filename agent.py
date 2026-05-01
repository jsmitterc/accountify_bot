"""Claude agent that turns a spoken utterance into an Accountify transaction."""
from __future__ import annotations

import json
from datetime import date as _date
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    create_sdk_mcp_server,
    query,
    tool,
)

from accountify_client import AccountifyClient, to_amount


SYSTEM_PROMPT = """You are an accounting assistant for Accountify, a double-entry bookkeeping app.

Your only job: convert a short spoken utterance about a personal expense into ONE balanced
double-entry transaction by calling the `create_transaction` tool exactly once.

The user prompt includes today's date, the utterance, and a JSON list of available accounts
(each with id, code, name, account_type, default_currency). Pick accounts ONLY from that list.

WORKFLOW
1. From the utterance, identify two accounts:
   - The ASSET or LIABILITY account that paid (e.g. "Commonwealth", "credit card") —
     match by case-insensitive substring on the name.
   - The EXPENSE account (e.g. "supermarket" → "Supermarket / Supermercado",
     "fuel" → "Transport", "coffee" → "Coffee Shop"). Pick the single closest expense-type account.
2. Call `create_transaction` once:
   - debit_account_id = the EXPENSE account's id
   - credit_account_id = the ASSET/LIABILITY account's id
   - amount = the numeric amount from the utterance
   - currency = the ISO 4217 code from the utterance (AUD/USD/EUR/GBP/...)
   - date = today unless the utterance specified a date (YYYY-MM-DD)
   - description = a short human-readable label (e.g. "Supermarket")
3. After the tool returns, output ONE short sentence confirming what was logged.
   Example: "Logged AUD 50.18 for Supermarket from Common Wealth Jonathan on 2026-04-27."
4. If you genuinely cannot match accounts (no plausible candidate), do NOT guess —
   output a single clarifying sentence starting with "I need clarification:" that
   names the ambiguity.

RULES
- Never invent account ids. Only use ids from the provided list.
- Never call `create_transaction` more than once.
- Amounts use a dot decimal separator (50.18, not 50,18).
- Keep the final reply to one sentence. No markdown, no headings.
"""


def build_tools(client: AccountifyClient, entity_id: str):
    """Build the in-process MCP server with the create_transaction tool."""

    @tool(
        "create_transaction",
        "Create a balanced double-entry transaction. The expense account is debited and the asset/liability account is credited by the same amount.",
        {
            "date": str,
            "description": str,
            "currency": str,
            "amount": str,
            "debit_account_id": str,
            "credit_account_id": str,
        },
    )
    async def create_transaction_tool(args: dict) -> dict:
        try:
            amount = to_amount(args["amount"])
            entries = [
                {
                    "account": args["debit_account_id"],
                    "debit_amount": amount,
                    "credit_amount": "0.00",
                    "currency": args["currency"],
                },
                {
                    "account": args["credit_account_id"],
                    "debit_amount": "0.00",
                    "credit_amount": amount,
                    "currency": args["currency"],
                },
            ]
            result = await client.create_transaction(
                entity_id=entity_id,
                date=args["date"],
                description=args["description"],
                entries=entries,
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "id": result.get("id"),
                                "is_balanced": result.get("is_balanced"),
                                "total_amount": result.get("total_amount"),
                            }
                        ),
                    }
                ]
            }
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"ERROR: {type(exc).__name__}: {exc}"}],
                "is_error": True,
            }

    return create_sdk_mcp_server(
        name="accountify",
        version="0.1.0",
        tools=[create_transaction_tool],
    )


async def run_agent(utterance: str, client: AccountifyClient, entity_id: str) -> str:
    """Run the agent over a single utterance and return Claude's final text."""
    accounts = await client.list_accounts(entity_id)
    slim_accounts = [
        {
            "id": a["id"],
            "code": a.get("code", ""),
            "name": a.get("name", ""),
            "account_type": a.get("account_type", ""),
            "default_currency": a.get("default_currency", "USD"),
        }
        for a in accounts
        if a.get("is_active", True)
    ]

    server = build_tools(client, entity_id)
    today = _date.today().isoformat()

    user_prompt = (
        f"Today: {today}\n"
        f"Utterance: {utterance.strip()}\n"
        f"Available accounts (JSON):\n{json.dumps(slim_accounts, indent=2)}"
    )

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"accountify": server},
        allowed_tools=["mcp__accountify__create_transaction"],
        max_turns=4,
    )

    final_text = ""
    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, ResultMessage):
            final_text = (message.result or "").strip()

    return final_text or "Sorry, I couldn't process that."
