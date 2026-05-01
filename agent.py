"""Claude agent that turns a spoken utterance into an Accountify transaction."""
from __future__ import annotations

import json
from datetime import date as _date
from typing import Any

import anthropic

from accountify_client import AccountifyClient, to_amount


MODEL = "claude-opus-4-7"
MAX_TURNS = 4

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

TOOLS: list[dict[str, Any]] = [
    {
        "name": "create_transaction",
        "description": (
            "Create a balanced double-entry transaction. The expense account is debited "
            "and the asset/liability account is credited by the same amount."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                "description": {"type": "string", "description": "Short human-readable label"},
                "currency": {"type": "string", "description": "ISO 4217 code, e.g. AUD"},
                "amount": {"type": "string", "description": "Decimal amount as a string, e.g. 50.18"},
                "debit_account_id": {"type": "string", "description": "Expense account id"},
                "credit_account_id": {"type": "string", "description": "Asset or liability account id"},
            },
            "required": [
                "date",
                "description",
                "currency",
                "amount",
                "debit_account_id",
                "credit_account_id",
            ],
        },
    }
]


async def _execute_create_transaction(
    args: dict[str, Any], client: AccountifyClient, entity_id: str
) -> str:
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
    return json.dumps(
        {
            "id": result.get("id"),
            "is_balanced": result.get("is_balanced"),
            "total_amount": result.get("total_amount"),
        }
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

    today = _date.today().isoformat()
    anthropic_client = anthropic.AsyncAnthropic()

    # Stable prefix (accounts list) is cached; volatile suffix (date + utterance) is not.
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"Available accounts (JSON):\n{json.dumps(slim_accounts, indent=2)}",
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": (
                        f"Today: {today}\n"
                        f"Utterance: {utterance.strip()}\n\n"
                        "Identify the two accounts and call create_transaction once."
                    ),
                },
            ],
        }
    ]

    for _ in range(MAX_TURNS):
        response = await anthropic_client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if b.type == "text"), "")
            return text.strip() or "Sorry, I couldn't process that."

        if response.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": response.content})

        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                result_text = await _execute_create_transaction(block.input, client, entity_id)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    }
                )
            except Exception as exc:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"ERROR: {type(exc).__name__}: {exc}",
                        "is_error": True,
                    }
                )

        messages.append({"role": "user", "content": tool_results})

    return "Sorry, I couldn't process that."
