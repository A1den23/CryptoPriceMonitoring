# Stablecoin Async Follow-up Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor the stablecoin depeg monitoring path so DefiLlama polling and stablecoin alert delivery no longer block the asyncio event loop, while keeping operator docs in sync.

**Architecture:** Keep the existing module boundaries, but make the stablecoin path truly async. Convert the DefiLlama client to expose an async fetch API while keeping parsing logic deterministic, convert stablecoin polling to async `run_once()` / `run()`, and preserve `monitor/ws_monitor.py` as an orchestration layer that simply runs the background task.

**Tech Stack:** Python 3.11, asyncio, aiohttp, unittest, Telegram Bot API, DefiLlama HTTP API

---

### Task 1: Refactor the DefiLlama client to async fetching

**Files:**
- Modify: `common/clients/defillama.py`
- Test: `tests/test_regressions.py`

**Step 1: Write the failing tests**

Add regression tests in `tests/test_regressions.py` for the async client fetch path.

Add tests such as:

```python
class DefiLlamaClientAsyncRegressionTests(unittest.TestCase):
    def test_defillama_client_fetch_stablecoins_uses_async_session(self):
        ...

    def test_defillama_client_fetch_stablecoins_raises_on_invalid_payload(self):
        ...
```

Test expectations:
- `fetch_stablecoins()` is awaitable
- it awaits the HTTP response JSON payload and returns parsed ranked snapshots
- invalid top-level payload still raises `ValueError`
- parsing logic remains independent from live HTTP

Use fake async response/session objects instead of real network calls.

**Step 2: Run tests to verify they fail**

Run:
```bash
python3 -m unittest tests.test_regressions.DefiLlamaClientAsyncRegressionTests
```

Expected: FAIL because the client is still synchronous.

**Step 3: Write minimal implementation**

Update `common/clients/defillama.py` to:
- create and manage an `aiohttp.ClientSession`
- expose `async def fetch_stablecoins(self, top_n: int)`
- keep `parse_stablecoins()` as the deterministic parser
- keep explicit timeout behavior
- provide clean session cleanup support

Implementation requirements:
- do not move parsing into the HTTP branch
- do not change the stablecoin record shape
- keep malformed payload behavior raising `ValueError`

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_regressions.DefiLlamaClientAsyncRegressionTests
```

Expected: PASS

**Step 5: Commit**

```bash
git add common/clients/defillama.py tests/test_regressions.py
git commit -m "refactor: make defillama client async"
```

---

### Task 2: Convert stablecoin polling and alert sending to async

**Files:**
- Modify: `monitor/stablecoin_depeg_monitor.py`
- Test: `tests/test_regressions.py`

**Step 1: Write the failing tests**

Add or update regression tests for the async stablecoin monitor flow.

Add tests such as:

```python
class StablecoinDepegMonitorAsyncPollingTests(unittest.TestCase):
    def test_stablecoin_monitor_run_once_awaits_async_client_and_sends_alerts(self):
        ...

    def test_stablecoin_monitor_run_continues_after_failed_async_poll(self):
        ...

    def test_stablecoin_monitor_run_propagates_cancelled_error(self):
        ...
```

Test expectations:
- `run_once()` is awaitable
- it awaits the async DefiLlama client fetch
- it does not call the Telegram notifier directly on the event loop path without an async boundary
- polling failures are logged and the loop continues
- `asyncio.CancelledError` is not swallowed by the loop

Use stub notifier/client objects and fake async sleep.

**Step 2: Run tests to verify they fail**

Run:
```bash
python3 -m unittest tests.test_regressions.StablecoinDepegMonitorAsyncPollingTests
```

Expected: FAIL because the monitor still uses synchronous `run_once()` and synchronous alert sending.

**Step 3: Write minimal implementation**

Update `monitor/stablecoin_depeg_monitor.py` to:
- make `run_once()` async
- await the DefiLlama client fetch
- add a small async alert-delivery helper
- use an async boundary for Telegram send so the event loop does not block
- keep the state machine logic and message formatting unchanged

Implementation requirements:
- `evaluate_snapshot()` may stay synchronous if it only decides state transitions and returns data needed for notification
- `run()` must continue on ordinary exceptions
- `run()` must let `asyncio.CancelledError` propagate
- avoid broad refactoring beyond the stablecoin path

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_regressions.StablecoinDepegMonitorAsyncPollingTests
```

Expected: PASS

**Step 5: Commit**

```bash
git add monitor/stablecoin_depeg_monitor.py tests/test_regressions.py
git commit -m "refactor: make stablecoin polling async"
```

---

### Task 3: Verify runtime orchestration still works with the async monitor

**Files:**
- Modify: `monitor/ws_monitor.py` (only if needed)
- Test: `tests/test_regressions.py`

**Step 1: Write the failing tests**

Adjust the stablecoin runtime integration tests so they assert the orchestration still works with async stablecoin monitor behavior.

Focus on these tests:

```python
class WebSocketMultiCoinMonitorStablecoinIntegrationTests(unittest.TestCase):
    def test_ws_monitor_starts_async_stablecoin_task_when_enabled(self):
        ...

    def test_ws_monitor_does_not_start_async_stablecoin_task_when_disabled(self):
        ...

    def test_ws_monitor_cancels_async_stablecoin_task_on_shutdown(self):
        ...
```

Test expectations:
- enabled config still starts the stablecoin task
- disabled config still leaves WebSocket-only behavior unchanged
- shutdown still cancels the stablecoin task cleanly

**Step 2: Run tests to verify they fail**

Run:
```bash
python3 -m unittest tests.test_regressions.WebSocketMultiCoinMonitorStablecoinIntegrationTests
```

Expected: FAIL if any integration assumptions still depend on the old sync stablecoin path.

**Step 3: Write minimal implementation**

Update `monitor/ws_monitor.py` only if the async refactor requires small orchestration fixes.

Implementation requirements:
- keep stablecoin monitor wiring at orchestration level only
- do not move stablecoin logic into WebSocket callbacks
- keep shutdown semantics and background-task cleanup intact

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_regressions.WebSocketMultiCoinMonitorStablecoinIntegrationTests
```

Expected: PASS

**Step 5: Commit**

```bash
git add monitor/ws_monitor.py tests/test_regressions.py
git commit -m "test: align websocket runtime with async stablecoin monitor"
```

---

### Task 4: Sync deployment and operator docs with shipped behavior

**Files:**
- Modify: `DEPLOYMENT.md`
- Modify: `README.md`
- Test: `tests/test_regressions.py`

**Step 1: Write the failing tests**

Add light regression tests that check operator docs include the stablecoin settings and configurable-threshold wording.

Add tests such as:

```python
class StablecoinDocumentationRegressionTests(unittest.TestCase):
    def test_deployment_doc_includes_stablecoin_depeg_settings(self):
        ...

    def test_readme_describes_stablecoin_threshold_as_configurable(self):
        ...
```

Test expectations:
- `DEPLOYMENT.md` mentions the five stablecoin env vars
- `README.md` describes the threshold as default ±5% and configurable via `STABLECOIN_DEPEG_THRESHOLD_PERCENT`

**Step 2: Run tests to verify they fail**

Run:
```bash
python3 -m unittest tests.test_regressions.StablecoinDocumentationRegressionTests
```

Expected: FAIL because deployment docs are not yet synchronized and README wording is still too hardcoded.

**Step 3: Write minimal implementation**

Update:
- `DEPLOYMENT.md` recommended config block to include all five stablecoin variables
- `README.md` stablecoin section to describe default ±5% behavior and mention the config variable explicitly

Implementation requirements:
- keep wording operator-focused
- do not expand documentation beyond what is needed to configure and understand the feature

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_regressions.StablecoinDocumentationRegressionTests
```

Expected: PASS

**Step 5: Commit**

```bash
git add DEPLOYMENT.md README.md tests/test_regressions.py
git commit -m "docs: sync stablecoin async follow-up docs"
```

---

### Task 5: Run full verification for the async follow-up

**Files:**
- Verify: `common/clients/defillama.py`
- Verify: `monitor/stablecoin_depeg_monitor.py`
- Verify: `monitor/ws_monitor.py`
- Verify: `README.md`
- Verify: `DEPLOYMENT.md`
- Verify: `tests/test_regressions.py`

**Step 1: Run the full regression suite**

Run:
```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: PASS

**Step 2: Run the import smoke test**

Run:
```bash
python3 -c "import common, monitor"
```

Expected: exits 0 with no import-time errors.

**Step 3: Manually verify the follow-up goals**

Check that:
- the DefiLlama client fetch path is async
- the stablecoin monitor no longer performs blocking DefiLlama fetches directly on the event loop
- stablecoin alert delivery uses an async boundary
- deployment docs now mention the stablecoin env vars
- README describes the stablecoin threshold as configurable, not hardcoded only

**Step 4: Commit**

```bash
git add common/clients/defillama.py monitor/stablecoin_depeg_monitor.py monitor/ws_monitor.py README.md DEPLOYMENT.md tests/test_regressions.py
git commit -m "refactor: make stablecoin monitoring fully async"
```
