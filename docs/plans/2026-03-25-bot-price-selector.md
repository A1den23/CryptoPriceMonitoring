# Bot Price Selector Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve `/price` so users can type `/price` without arguments, choose from enabled coins, and then view a richer single-coin detail page.

**Architecture:** Keep the existing bot command and callback flow intact. Only change the `/price` no-argument branch, expand the single-coin message renderer, and reuse the existing `price_<coin>` callback contract and keyboard helpers. Use `unittest` and small TDD steps so command behavior, button behavior, and detail rendering stay stable.

**Tech Stack:** Python 3.11, unittest, asyncio, python-telegram-bot, existing bot handler/message helper structure

---

### Task 1: Add rendering support for the `/price` coin picker and richer coin detail message

**Files:**
- Modify: `bot/messages.py:14-174`
- Test: `tests/test_bot_messages.py`

**Step 1: Write the failing tests**

Create `tests/test_bot_messages.py` with rendering-only tests:

```python
import unittest
from types import SimpleNamespace

from bot.messages import render_price_picker_message, render_price_detail_message


class BotMessagesTests(unittest.TestCase):
    def test_render_price_picker_message_prompts_user_to_choose_coin(self) -> None:
        self.assertIn("请选择", render_price_picker_message())

    def test_render_price_detail_message_includes_monitoring_fields(self) -> None:
        coin_config = SimpleNamespace(
            coin_name="BTC",
            enabled=True,
            symbol="BTCUSDT",
            integer_threshold=1000.0,
            volatility_percent=3.0,
            volatility_window=60,
        )

        message = render_price_detail_message(
            coin_name="BTC",
            coin_config=coin_config,
            price=64000.12,
            timestamp="2026-03-25 12:00:00",
            threshold_text="1,000",
        )

        self.assertIn("BTC", message)
        self.assertIn("BTCUSDT", message)
        self.assertIn("64000.12", message)
        self.assertIn("1,000", message)
        self.assertIn("3.0%/60s", message)
        self.assertIn("已启用", message)
        self.assertIn("2026-03-25 12:00:00", message)
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_bot_messages
```

Expected: FAIL because the new renderer functions do not exist yet.

**Step 3: Write minimal implementation**

In `bot/messages.py`, add:

- `render_price_picker_message()` returning a short prompt such as `"📌 <b>请选择要查看的币种</b>"`
- `render_price_detail_message(...)` that formats:
  - coin name
  - trading pair symbol
  - current price
  - milestone threshold text
  - volatility threshold/window
  - enabled status text
  - timestamp

Use the existing `format_price()` helper for price formatting. Do not add new abstractions beyond these two renderers.

**Step 4: Run test to verify it passes**

Run:
```bash
python3 -m unittest tests.test_bot_messages
```

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_bot_messages.py bot/messages.py
git commit -m "feat: add bot price picker renderers"
```

---

### Task 2: Change `/price` so no-argument usage shows enabled coin buttons

**Files:**
- Modify: `bot/handlers.py:39-87`
- Test: `tests/test_bot_handlers.py`

**Step 1: Write the failing test**

Create `tests/test_bot_handlers.py` with a focused command test:

```python
import types
import unittest
from unittest.mock import AsyncMock

from bot import handlers


class PriceCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_price_command_without_args_shows_coin_picker(self) -> None:
        reply_text = AsyncMock()
        update = types.SimpleNamespace(
            message=types.SimpleNamespace(reply_text=reply_text),
            effective_chat=types.SimpleNamespace(id=123),
        )
        context = types.SimpleNamespace(args=[])
        bot_self = types.SimpleNamespace(
            config=types.SimpleNamespace(
                coin_names=["BTC", "ETH"],
                get_enabled_coins=lambda: [
                    types.SimpleNamespace(coin_name="BTC"),
                    types.SimpleNamespace(coin_name="ETH"),
                ],
            ),
            _build_start_keyboard=lambda: object(),
        )

        await handlers.price_command(bot_self, update, context)

        reply_text.assert_awaited_once()
        self.assertIn("请选择", reply_text.await_args.kwargs["text"])
        self.assertIsNotNone(reply_text.await_args.kwargs["reply_markup"])
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_bot_handlers.PriceCommandTests.test_price_command_without_args_shows_coin_picker
```

Expected: FAIL because `/price` without args currently returns an input error and no picker.

**Step 3: Write minimal implementation**

In `bot/handlers.py`:

- import the new `render_price_picker_message`
- change the `else:` branch inside `price_command()` so it sends:
  - `text=render_price_picker_message()`
  - `reply_markup=self._build_start_keyboard()`
- remove the old no-argument error message from this branch
- keep all existing validation logic for the branch where args are present

**Step 4: Run test to verify it passes**

Run:
```bash
python3 -m unittest tests.test_bot_handlers.PriceCommandTests.test_price_command_without_args_shows_coin_picker
```

Expected: PASS

**Step 5: Add a regression test for `/price BTC`**

In the same test file, add a second test verifying that when `context.args == ["BTC"]`, `price_command()` still calls `self.send_price_update(...)` instead of returning the picker.

**Step 6: Run the handler test file**

Run:
```bash
python3 -m unittest tests.test_bot_handlers
```

Expected: PASS

**Step 7: Commit**

```bash
git add tests/test_bot_handlers.py bot/handlers.py bot/messages.py
git commit -m "feat: show coin picker for bare price command"
```

---

### Task 3: Upgrade the single-coin response to a richer detail view

**Files:**
- Modify: `bot/handlers.py:137-185`
- Modify: `bot/messages.py:160-174`
- Test: `tests/test_bot_handlers.py`

**Step 1: Write the failing test**

Add a handler-level detail test to `tests/test_bot_handlers.py`:

```python
    async def test_send_price_update_renders_coin_detail_view(self) -> None:
        sent_messages = []

        async def fake_send_or_edit_message(chat_id, text, message=None, reply_markup=None):
            sent_messages.append({
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
            })

        coin_config = types.SimpleNamespace(
            coin_name="BTC",
            enabled=True,
            symbol="BTCUSDT",
            integer_threshold=1000.0,
            volatility_percent=3.0,
            volatility_window=60,
        )
        bot_self = types.SimpleNamespace(
            config=types.SimpleNamespace(get_coin_config=lambda coin: coin_config),
            _get_price=AsyncMock(return_value=64000.12),
            _format_timestamp=lambda: "2026-03-25 12:00:00",
            _format_threshold=lambda config: "1,000",
            _build_price_keyboard=lambda coin: f"keyboard:{coin}",
            _send_or_edit_message=fake_send_or_edit_message,
        )

        await handlers.send_price_update(bot_self, 123, "BTC")

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("BTCUSDT", sent_messages[0]["text"])
        self.assertIn("里程碑", sent_messages[0]["text"])
        self.assertIn("波动告警", sent_messages[0]["text"])
        self.assertEqual(sent_messages[0]["reply_markup"], "keyboard:BTC")
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_bot_handlers.PriceCommandTests.test_send_price_update_renders_coin_detail_view
```

Expected: FAIL because the current single-coin renderer only shows coin name, price, symbol, and timestamp.

**Step 3: Write minimal implementation**

In `bot/messages.py`, replace the current single-coin response renderer with a richer renderer such as:

```python
def render_price_detail_message(
    coin_name: str,
    coin_config,
    price: float,
    timestamp: str,
    threshold_text: str,
) -> str:
    emoji = get_coin_emoji(coin_name)
    status_text = "已启用" if coin_config.enabled else "未启用"
    return (
        f"{emoji} <b>{coin_name}</b> 详情\n"
        f"💰 当前价格：{format_price(price)}\n"
        f"📈 交易对：{coin_config.symbol}\n"
        f"📍 里程碑：每 {threshold_text}\n"
        f"📊 波动告警：{coin_config.volatility_percent}%/{coin_config.volatility_window}s\n"
        f"⚙️ 状态：{status_text}\n"
        f"⏱️ {timestamp}"
    )
```

Then update `send_price_update()` in `bot/handlers.py` to call this renderer with:

- `coin_name`
- `coin_config`
- fetched `price`
- `self._format_timestamp()`
- `self._format_threshold(coin_config)`

Do not change unknown-coin or disabled-coin error branches.

**Step 4: Run test to verify it passes**

Run:
```bash
python3 -m unittest tests.test_bot_handlers.PriceCommandTests.test_send_price_update_renders_coin_detail_view
```

Expected: PASS

**Step 5: Run the related bot test files**

Run:
```bash
python3 -m unittest tests.test_bot_handlers tests.test_bot_messages tests.test_bot_app
```

Expected: PASS

**Step 6: Commit**

```bash
git add tests/test_bot_handlers.py tests/test_bot_messages.py tests/test_bot_app.py bot/handlers.py bot/messages.py
git commit -m "feat: expand bot single-coin detail view"
```

---

### Task 4: Verify callback and help-text behavior remains coherent

**Files:**
- Modify: `bot/messages.py:92-126`
- Test: `tests/test_bot_messages.py`
- Test: `tests/test_bot_app.py:279-339`

**Step 1: Write the failing tests**

Add two tests:

1. In `tests/test_bot_messages.py`, verify help text explains both usages:

```python
    def test_render_help_message_mentions_picker_and_direct_coin_lookup(self) -> None:
        enabled_coins = [SimpleNamespace(coin_name="BTC", symbol="BTCUSDT")]
        message = render_help_message(enabled_coins)
        self.assertIn("/price", message)
        self.assertIn("/price BTC", message)
        self.assertIn("选择", message)
```

2. In `tests/test_bot_app.py`, verify helper exposure still matches app usage if a new helper wrapper is added. Only add this test if `TelegramBot` grows a new wrapper method; otherwise skip this step and keep app tests unchanged.

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_bot_messages.BotMessagesTests.test_render_help_message_mentions_picker_and_direct_coin_lookup
```

Expected: FAIL because the current help text only documents `/price [coin]`.

**Step 3: Write minimal implementation**

In `bot/messages.py`, adjust help text so `/price` is documented like this:

```text
/price - 弹出正在监控的币种列表
/price BTC - 直接查询指定币种详情
```

Keep the rest of the help message structure unchanged.

**Step 4: Run test to verify it passes**

Run:
```bash
python3 -m unittest tests.test_bot_messages.BotMessagesTests.test_render_help_message_mentions_picker_and_direct_coin_lookup
```

Expected: PASS

**Step 5: Run the full bot-focused verification suite**

Run:
```bash
python3 -m unittest tests.test_bot_handlers tests.test_bot_messages tests.test_bot_app tests.test_entrypoints
```

Expected: PASS

**Step 6: Run the project regression suite**

Run:
```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: PASS

**Step 7: Commit**

```bash
git add tests/test_bot_handlers.py tests/test_bot_messages.py tests/test_bot_app.py tests/test_entrypoints.py bot/handlers.py bot/messages.py bot/app.py
git commit -m "feat: improve bot price query flow"
```

---

### Task 5: Final verification and docs sync

**Files:**
- Modify: `README.md:45-85`
- Modify: `DEPLOYMENT.md:126-149`

**Step 1: Write the doc updates**

Update command descriptions so they reflect:

- `/price` can open a monitored-coin selector
- `/price BTC` directly opens the detail view for BTC

Keep wording concise and aligned with actual bot behavior.

**Step 2: Run focused verification on docs-sensitive contracts**

Run:
```bash
python3 -m unittest tests.test_deployment_contracts tests.test_entrypoints
```

Expected: PASS

**Step 3: Run the real bot entrypoint check**

Run:
```bash
python3 -m bot
```

Expected: Bot starts successfully and command registration still works. Stop it after verifying startup if needed.

**Step 4: Run final full verification**

Run:
```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: PASS

**Step 5: Commit**

```bash
git add README.md DEPLOYMENT.md tests/test_bot_handlers.py tests/test_bot_messages.py tests/test_bot_app.py tests/test_entrypoints.py bot/handlers.py bot/messages.py bot/app.py
git commit -m "docs: document enhanced bot price command"
```
