"""Claude agent that turns a spoken utterance into an Accountify action."""
from __future__ import annotations

import json
from datetime import date as _date
from typing import Any

import anthropic

from accountify_client import AccountifyClient, to_amount


MODEL = "claude-opus-4-7"
MAX_TURNS = 4

VALID_ACCOUNT_TYPES = ("asset", "liability", "equity", "revenue", "expense")

SYSTEM_PROMPT = """You are an accounting assistant for Accountify, a double-entry bookkeeping app.

You handle two kinds of spoken requests:

INTENT 1 — LOG AN EXPENSE
Trigger words: "add", "log", "spent", "paid", "bought", any utterance with a numeric amount + currency.
Tool: `create_transaction` (called exactly once).

Workflow:
1. Pick two accounts from the available accounts list:
   - Paying account: the ASSET or LIABILITY (e.g. "Commonwealth", "credit card") matched by case-insensitive substring on name.
   - Expense account: the closest EXPENSE-type account (e.g. "supermarket" → "Supermarket", "fuel" → "Transport").
2. Call `create_transaction` once:
   - debit_account_id = the EXPENSE account's id
   - credit_account_id = the ASSET/LIABILITY account's id
   - amount, currency, date (today unless specified), description
3. Reply with one short sentence confirming.
   Example: "Logged AUD 50.18 for Supermarket from Commonwealth on 2026-04-27."
4. If you can't confidently match an account, reply with one sentence starting "I need clarification:".

INTENT 2 — CREATE AN ACCOUNT
Trigger words: "create", "add a new account", "new account".
Tool: `create_account` (called exactly once).

Workflow — perform ALL checks before calling the tool. Stop at the first failure.
1. account_type — MUST be one of: asset, liability, equity, revenue, expense.
   The user may say it directly ("create an EXPENSE account") or via a synonym
   ("expense" / "expenses", "asset" / "bank" / "cash", "liability" / "credit card" / "loan",
    "income" / "revenue", "equity").
   - If missing or ambiguous, reply: "I need clarification: what type of account? (asset, liability, equity, revenue, or expense)"
2. name — must not already exist. Check the available accounts list (case-insensitive substring match on name).
   - If it exists, reply: "An account named '<name>' already exists."
3. default_currency — REQUIRED. ISO 4217 code (AUD, USD, EUR, GBP, ...).
   - If the user did not name a currency, reply: "I need clarification: what currency? (e.g. AUD, USD, EUR)"
4. owner_entity_id — REQUIRED. The entity that owns the account.
   - The user MUST name the owning entity in the utterance. Do not auto-pick, even if there is only one entity available.
   - If the user named one, match it (case-insensitive substring) against the entities list and use that id.
   - If the user did NOT name one, reply: "I need clarification: which entity should own it? (<comma-separated entity names from the list>)"
5. Call `create_account` once with all four fields:
   - name (Title Case, e.g. "entertainment" → "Entertainment")
   - account_type
   - default_currency
   - owner_entity_id
6. Reply with one short sentence confirming.
   Example: "Created expense account 'Entertainment' (AUD)."

GENERAL RULES
- Never invent account ids. Use ids only from the provided list.
- Never call any tool more than once per request.
- Amounts use a dot decimal separator (50.18, not 50,18).
- One sentence reply. No markdown, no headings.
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
    },
    {
        "name": "create_account",
        "description": (
            "Create a new account on the chart of accounts. Only call after verifying the "
            "name does not exist, account_type is valid, and currency + owner_entity_id are known."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Account name, Title Case"},
                "account_type": {
                    "type": "string",
                    "enum": list(VALID_ACCOUNT_TYPES),
                    "description": "One of: asset, liability, equity, revenue, expense",
                },
                "default_currency": {
                    "type": "string",
                    "description": "ISO 4217 code (e.g. AUD, USD, EUR)",
                },
                "owner_entity_id": {
                    "type": "string",
                    "description": "UUID of the owning entity, taken from the entities list in context",
                },
            },
            "required": ["name", "account_type", "default_currency", "owner_entity_id"],
        },
    },
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


async def _execute_create_account(
    args: dict[str, Any], client: AccountifyClient, entity_id: str
) -> str:
    account_type = args["account_type"]
    if account_type not in VALID_ACCOUNT_TYPES:
        raise ValueError(f"invalid account_type {account_type!r}; must be one of {VALID_ACCOUNT_TYPES}")
    result = await client.create_account(
        entity_id=args["owner_entity_id"],
        name=args["name"],
        account_type=account_type,
        default_currency=args["default_currency"],
    )
    return json.dumps(
        {
            "id": result.get("id"),
            "code": result.get("code"),
            "name": result.get("name"),
            "account_type": result.get("account_type"),
            "default_currency": result.get("default_currency"),
            "owner_entity": result.get("owner_entity", {}).get("name") if isinstance(result.get("owner_entity"), dict) else None,
        }
    )


async def _dispatch_tool(
    name: str, args: dict[str, Any], client: AccountifyClient, entity_id: str
) -> str:
    if name == "create_transaction":
        return await _execute_create_transaction(args, client, entity_id)
    if name == "create_account":
        return await _execute_create_account(args, client, entity_id)
    raise ValueError(f"unknown tool {name!r}")


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

    entities = await client.list_entities()
    slim_entities = [
        {
            "id": e["id"],
            "name": e.get("name", ""),
            "default_currency": e.get("default_currency", ""),
        }
        for e in entities
        if e.get("is_active", True)
    ]

    today = _date.today().isoformat()
    anthropic_client = anthropic.AsyncAnthropic()

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Available entities (JSON):\n{json.dumps(slim_entities, indent=2)}\n\n"
                        f"Available accounts (JSON):\n{json.dumps(slim_accounts, indent=2)}"
                    ),
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": (
                        f"Today: {today}\n"
                        f"Current entity for transactions: {entity_id}\n"
                        f"Utterance: {utterance.strip()}\n\n"
                        "Decide the intent (log expense or create account) and act."
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
                result_text = await _dispatch_tool(block.name, block.input, client, entity_id)
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
