# Telegram Stablecoins Command Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `/stablecoins` Telegram command that returns the latest top 20 stablecoin prices on demand.

**Architecture:** Extend the existing Telegram bot command surface with one new async handler, reuse the existing async `DefiLlamaClient` fetch path for stablecoin snapshots, and add a dedicated message renderer for stablecoin list output. Keep this command independent from the background stablecoin depeg monitor so users can query stablecoins manually even when monitoring is disabled.

**Tech Stack:** Python 3.11, asyncio, python-telegram-bot, aiohttp, unittest

---

### Task 1: Add regression tests for the new Telegram command

**Files:**
- Modify: `tests/test_regressions.py`
- Reference: `bot/handlers.py`
- Reference: `bot/messages.py`

**Step 1: Write the failing tests**

Add bot regression tests near `TelegramBotRegressionTests` for the new stablecoin command behavior.

Add tests shaped like:

```python
class TelegramBotStablecoinCommandRegressionTests(unittest.TestCase):
    def test_stablecoins_command_returns_formatted_top_20_list(self) -> None:
        ...

    def test_stablecoins_command_returns_error_message_when_fetch_fails(self) -> None:
        ...

    def test_help_message_mentions_stablecoins_command(self) -> None:
        ...
```

Test details:
- instantiate `bot.TelegramBot(...)` with a minimal `types.SimpleNamespace` config containing `telegram_bot_token` and `get_enabled_coins`
- replace `telegram_bot._send_or_edit_message` with `AsyncMock()` so the test can assert the rendered output without real Telegram I/O
- replace or patch the stablecoin fetch path with an async fake returning real `StablecoinSnapshot` objects from `common.clients.defillama`
- for the success case, assert the sent text includes:
  - `前20稳定币价格`
  - `#1`
  - at least one stablecoin symbol/name pair such as `USDT` and `Tether`
  - a deviation string relative to `$1`
- for the failure case, make the async fetch raise an exception and assert the bot sends a concise error message mentioning stablecoin price retrieval failure
- for the help case, assert the help/welcome copy contains `/stablecoins`

Use patterns already present in `tests/test_regressions.py`, especially:
- `TelegramBotRegressionTests`
- `DefiLlamaClientAsyncRegressionTests`
- `types.SimpleNamespace`
- `AsyncMock`

**Step 2: Run tests to verify they fail**

Run:
```bash
python3 -m unittest tests.test_regressions.TelegramBotStablecoinCommandRegressionTests
```

Expected: FAIL because `/stablecoins` is not implemented and help text does not mention it yet.

**Step 3: Write minimal test support code only if needed**

If the test needs a tiny helper fake such as a simple async context manager or a stub update/message object, add the smallest possible helper in `tests/test_regressions.py` near the existing fake classes:

```python
class FakeTelegramMessage:
    def __init__(self) -> None:
        self.reply_text = AsyncMock()
```

Do not add reusable abstractions unless the tests genuinely need them more than once.

**Step 4: Run tests again to keep the failure focused**

Run:
```bash
python3 -m unittest tests.test_regressions.TelegramBotStablecoinCommandRegressionTests
```

Expected: FAIL only for the missing command/help behavior, not for broken test scaffolding.

**Step 5: Commit the failing tests**

```bash
git add tests/test_regressions.py
git commit -m "test: cover telegram stablecoins command"
```

---

### Task 2: Implement the `/stablecoins` command and message rendering

**Files:**
- Modify: `bot/app.py`
- Modify: `bot/handlers.py`
- Modify: `bot/messages.py`
- Modify: `bot/__init__.py`
- Reference: `common/clients/defillama.py`

**Step 1: Register the new command handler**

In `bot/app.py`, add the new command registration alongside the existing command handlers:

```python
self.application.add_handler(CommandHandler("stablecoins", self.stablecoins_command))
```

Also bind the handler method at the bottom of the file with the existing pattern:

```python
TelegramBot.stablecoins_command = handlers.stablecoins_command
```

**Step 2: Add the stablecoin message renderer**

In `bot/messages.py`, add a focused rendering helper that accepts the stablecoin snapshots and timestamp.

Target shape:

```python
def render_stablecoin_prices_message(stablecoins: list[StablecoinSnapshot], timestamp: str) -> str:
    message = "🪙 <b>前20稳定币价格</b>\n\n"
    ...
    return f"{message}\n⏱️ {timestamp}"
```

Implementation requirements:
- import `StablecoinSnapshot` from `common`
- include rank, symbol, name, price, and deviation from `$1`
- format deviation as signed percent like `+0.12%` or `-5.70%`
- keep the message concise enough for Telegram
- if the list is empty, render a short empty-state message instead of crashing

**Step 3: Implement the async command handler**

In `bot/handlers.py`, add:

```python
async def stablecoins_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...
```

Implementation requirements:
- create a `DefiLlamaClient` inside the command path using `async with DefiLlamaClient() as client:`
- await `client.fetch_stablecoins(top_n=20)`
- render the response with the new message helper
- send the response using `update.message.reply_text(..., parse_mode="HTML", disable_notification=False)` or `self._send_or_edit_message(...)`
- catch ordinary exceptions, log them, and send a short Chinese error message such as:

```python
"❌ 获取前20稳定币价格失败"
```

Do not:
- reuse the background stablecoin monitor state
- gate this command on `STABLECOIN_DEPEG_MONITOR_ENABLED`
- add pagination or refresh buttons in this task

**Step 4: Update user-facing command text**

In `bot/messages.py` and `bot/__init__.py`, add `/stablecoins` to:
- the `/start` welcome message command list
- the `/help` message command list
- the startup notification text in `bot/__init__.py`

Use wording consistent with the existing Chinese command descriptions, for example:

```text
/stablecoins - 查看前20稳定币价格
```

**Step 5: Run the new regression tests**

Run:
```bash
python3 -m unittest tests.test_regressions.TelegramBotStablecoinCommandRegressionTests
```

Expected: PASS

**Step 6: Commit the implementation**

```bash
git add bot/app.py bot/handlers.py bot/messages.py bot/__init__.py tests/test_regressions.py
git commit -m "feat: add telegram stablecoins command"
```

---

### Task 3: Verify existing bot behavior still works

**Files:**
- Verify: `bot/app.py`
- Verify: `bot/handlers.py`
- Verify: `bot/messages.py`
- Verify: `bot/__init__.py`
- Verify: `tests/test_regressions.py`

**Step 1: Run the bot-focused regression slice**

Run:
```bash
python3 -m unittest tests.test_regressions.TelegramBotRegressionTests tests.test_regressions.TelegramBotStablecoinCommandRegressionTests tests.test_regressions.MainEntrypointRegressionTests
```

Expected: PASS

**Step 2: Run the full regression suite**

Run:
```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: PASS

**Step 3: Run the import smoke test**

Run:
```bash
python3 -c "import bot, common, monitor"
```

Expected: exits 0 with no import-time errors.

**Step 4: Manually verify the shipped behavior**

Check that:
- `/stablecoins` is registered in the Telegram bot command set
- the command fetches stablecoin data through `DefiLlamaClient`
- the output includes top-20 rank, symbol, name, price, and deviation from `$1`
- the command remains available even if stablecoin depeg monitoring is disabled
- welcome/help/startup command text mentions `/stablecoins`

**Step 5: Commit the verification checkpoint**

```bash
git add bot/app.py bot/handlers.py bot/messages.py bot/__init__.py tests/test_regressions.py
git commit -m "test: verify telegram stablecoins command"
```
