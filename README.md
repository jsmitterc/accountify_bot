# Accountify Siri Expense Bot

Siri (iPhone) → Shortcut → Cloudflare Tunnel → local FastAPI bot → Claude Agent → Accountify Django API.

You say *"Hey Siri, log expense"*, then dictate *"50.18 AUD to supermarket from Commonwealth"*. The agent
parses it, picks the right accounts, and posts a balanced double-entry transaction.

---

## 1. Install

```powershell
cd C:\Users\Jonathan.Smitter\Documents\JS_SOFTWARE_SOLUTIONS_LTD\accountify\bot
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Configure

```powershell
copy .env.example .env
# generate a strong shared secret:
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Open `.env` and fill in:

- `ACCOUNTIFY_USERNAME` / `ACCOUNTIFY_PASSWORD` — your Accountify login.
- `BOT_SHARED_SECRET` — paste the token you just generated.
- `ACCOUNTIFY_ENTITY_ID` — leave blank to auto-pick the first entity, or paste a UUID.

## 3. Smoke test (no Siri yet)

Make sure the Django backend is running on `http://localhost:8000`. Then:

```powershell
python smoke_test.py "add 50.18 aud to supermarket from commonwealth"
```

You should see the agent print a one-line confirmation. **Verify in the Accountify UI that the
transaction actually landed before going further.** If accounts didn't match, the agent will say
"I need clarification: ..." — that's expected behavior, not a bug.

## 4. Run the HTTP server

```powershell
python main.py
```

In another terminal:

```powershell
.\test_http.ps1 "add 50.18 aud to supermarket from commonwealth"
```

## 5. Expose with Cloudflare Tunnel

You need the public HTTPS URL so Siri can reach your laptop.

### Install cloudflared (one-time)
Download from <https://github.com/cloudflare/cloudflared/releases/latest> (pick `cloudflared-windows-amd64.exe`),
rename to `cloudflared.exe`, put it on your PATH (e.g. `C:\Windows\System32\`).

### Quick tunnel (no Cloudflare account needed, throwaway URL)
```powershell
cloudflared tunnel --url http://127.0.0.1:8787
```
You'll see something like `https://random-words-1234.trycloudflare.com`. Use that URL in the Shortcut.
Note: this URL changes every restart. Fine for testing.

### Named tunnel (stable URL, requires free Cloudflare account + your own domain)
```powershell
cloudflared tunnel login
cloudflared tunnel create accountify-bot
cloudflared tunnel route dns accountify-bot bot.yourdomain.com
cloudflared tunnel run --url http://127.0.0.1:8787 accountify-bot
```

## 6. iPhone Shortcut

Open the **Shortcuts** app on your iPhone → **+** to create a new shortcut. Name it **"Log expense"**
(this becomes the Siri trigger phrase).

Add these actions in order:

1. **Dictate Text**
   - Stop Listening: **On Tap** *(or "After Pause" if you prefer)*
   - Language: English

2. **Get Contents of URL**
   - URL: `https://YOUR-TUNNEL-URL/expense`
   - Method: **POST**
   - Headers:
     - `X-Bot-Secret` = *(paste the same secret from your `.env`)*
     - `Content-Type` = `application/json`
   - Request Body: **JSON**
     - `text` (Text) = *Dictated Text* (tap the variable picker)

3. **Get Dictionary Value**
   - Get: Value for `reply` in *Contents of URL*

4. **Speak Text**
   - Text: *Dictionary Value*

Save. Now say:

> **"Hey Siri, log expense"**

Siri will listen, send the dictation to your bot, and read back the confirmation.

---

## How utterances map to accounting

> "Add 50.18 AUD to my supermarket expense paid with my Commonwealth account"

Becomes a single balanced transaction:

| Entry | Account                    | Debit  | Credit |
|-------|----------------------------|--------|--------|
| 1     | Groceries (expense)        | 50.18  | 0.00   |
| 2     | Commonwealth (asset)       | 0.00   | 50.18  |

Both in AUD. Date = today unless you spoke a different one.

## Troubleshooting

- **"login failed: 401"** — check Accountify creds in `.env`.
- **"I need clarification: ..."** — the agent couldn't confidently match an account name. Either say it more explicitly
  ("paid with my Commonwealth Checking account") or rename the account in Accountify.
- **Siri reads gibberish** — open the Shortcut, run it manually, and inspect the response. The shared secret may be wrong (you'll get HTTP 401), or the tunnel may be down.
- **Tunnel URL changed** — quick tunnels rotate. Re-edit the URL in the Shortcut, or move to a named tunnel.

## Files
- `accountify_client.py` — async HTTP client with JWT auth + auto-refresh.
- `agent.py` — Claude Agent SDK setup + tool definitions + system prompt.
- `main.py` — FastAPI app exposing `POST /expense` and `GET /health`.
- `smoke_test.py` — direct agent test, no HTTP layer.
- `test_http.ps1` — PowerShell hit against the running server.
