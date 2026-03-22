# Stablecoin Depeg Monitor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an optional DefiLlama-backed stablecoin depeg monitor that watches the top 20 stablecoins and alerts via Telegram when any stablecoin deviates from $1 by more than 5%.

**Architecture:** Keep the existing Binance WebSocket monitor unchanged and add a parallel async stablecoin polling loop inside the monitor runtime. Isolate DefiLlama fetching in a dedicated client, isolate depeg state/alerting in a dedicated monitor module, and wire both into `WebSocketMultiCoinMonitor.run()` so they share lifecycle, logging, and notification behavior.

**Tech Stack:** Python 3.11, asyncio, aiohttp, unittest, requests, Telegram Bot API, DefiLlama HTTP API

---

### Task 1: Add stablecoin depeg configuration parsing

**Files:**
- Modify: `common/config.py`
- Test: `tests/test_regressions.py`

**Step 1: Write the failing tests**

Add regression tests in `tests/test_regressions.py` that verify `ConfigManager` reads these new environment variables correctly:

- `STABLECOIN_DEPEG_MONITOR_ENABLED`
- `STABLECOIN_DEPEG_TOP_N`
- `STABLECOIN_DEPEG_THRESHOLD_PERCENT`
- `STABLECOIN_DEPEG_POLL_INTERVAL_SECONDS`
- `STABLECOIN_DEPEG_ALERT_COOLDOWN_SECONDS`

Test cases to add:

```python
def test_config_manager_reads_stablecoin_depeg_settings(self):
    ...

def test_config_manager_falls_back_to_stablecoin_depeg_defaults(self):
    ...
```

Assert exact values for:
- enabled default
- top N default `20`
- threshold default `5.0`
- poll interval default `300`
- cooldown default `3600`

**Step 2: Run tests to verify they fail**

Run:
```bash
python3 -m unittest tests.test_regressions.ConfigManagerRegressionTests
```

Expected: FAIL because these config attributes do not exist yet.

**Step 3: Write minimal implementation**

Update `common/config.py` to parse and expose new config attributes on `ConfigManager` using the existing safe helpers.

Implementation requirements:
- Boolean parsing should follow the current `lower() == "true"` style.
- Integer/float parsing should use `_safe_int_env` / `_safe_float_env`.
- Keep defaults conservative:
  - enabled: `false`
  - top N: `20`
  - threshold percent: `5.0`
  - poll interval: `300`
  - cooldown: `3600`
- Do not change current `COIN_LIST` behavior.

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_regressions.ConfigManagerRegressionTests
```

Expected: PASS

**Step 5: Commit**

```bash
git add common/config.py tests/test_regressions.py
git commit -m "feat: add stablecoin depeg config settings"
```

---

### Task 2: Add a DefiLlama stablecoin API client

**Files:**
- Create: `common/clients/defillama.py`
- Modify: `common/__init__.py`
- Test: `tests/test_regressions.py`

**Step 1: Write the failing tests**

Add tests covering response parsing and top-N selection. Use mocked HTTP responses rather than live network calls.

Add data-oriented tests such as:

```python
def test_defillama_client_parses_top_stablecoins(self):
    payload = {
        "peggedAssets": [
            {"name": "USDC", "symbol": "USDC", "price": 0.943, "circulating": 1_000},
            {"name": "USDT", "symbol": "USDT", "price": 1.0, "circulating": 2_000},
        ]
    }
    ...


def test_defillama_client_skips_invalid_entries(self):
    payload = {
        "peggedAssets": [
            {"name": "USDC", "symbol": "USDC", "price": 0.943, "circulating": 1_000},
            {"name": None, "symbol": "BAD", "price": "oops", "circulating": 100},
        ]
    }
    ...
```

Expectations:
- valid rows are parsed into simple stablecoin records
- entries missing required fields are skipped
- results are sorted descending by circulating/market-cap proxy from API payload
- top-N limit is honored

**Step 2: Run tests to verify they fail**

Run:
```bash
python3 -m unittest tests.test_regressions.DefiLlamaClientRegressionTests
```

Expected: FAIL because the client does not exist yet.

**Step 3: Write minimal implementation**

Create `common/clients/defillama.py` with:
- a small immutable record/dataclass for stablecoin snapshot rows
- a DefiLlama client using `requests.Session()` like existing clients
- a parsing helper that extracts top stablecoins from the API payload
- a fetch method that requests the stablecoin payload and returns parsed top-N rows

Implementation requirements:
- keep the client read-only
- keep request timeout explicit
- raise/log cleanly on malformed payloads
- prefer a parser method separate from HTTP so tests can stay deterministic

Then expose the client from `common/__init__.py` using the lazy export pattern.

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_regressions.DefiLlamaClientRegressionTests
```

Expected: PASS

**Step 5: Commit**

```bash
git add common/clients/defillama.py common/__init__.py tests/test_regressions.py
git commit -m "feat: add defillama stablecoin client"
```

---

### Task 3: Add stablecoin depeg state machine and alert formatting

**Files:**
- Create: `monitor/stablecoin_depeg_monitor.py`
- Test: `tests/test_regressions.py`

**Step 1: Write the failing tests**

Add focused tests for threshold behavior and state transitions.

Add tests such as:

```python
def test_stablecoin_monitor_does_not_alert_within_threshold(self):
    ...  # 1.049 and 0.951 should not alert


def test_stablecoin_monitor_alerts_when_price_exceeds_upper_threshold(self):
    ...  # 1.051 alerts


def test_stablecoin_monitor_alerts_when_price_exceeds_lower_threshold(self):
    ...  # 0.949 alerts


def test_stablecoin_monitor_respects_per_coin_cooldown(self):
    ...


def test_stablecoin_monitor_resets_after_returning_to_normal(self):
    ...
```

Use a stub notifier and fake clock pattern consistent with existing regression tests.

**Step 2: Run tests to verify they fail**

Run:
```bash
python3 -m unittest tests.test_regressions.StablecoinDepegMonitorRegressionTests
```

Expected: FAIL because the monitor implementation does not exist yet.

**Step 3: Write minimal implementation**

Create `monitor/stablecoin_depeg_monitor.py` with:
- a small per-stablecoin state record
- a monitor class that:
  - accepts config + notifier + client dependencies
  - evaluates one stablecoin snapshot at a time
  - computes signed deviation from $1
  - decides whether to alert now
  - formats alert messages

Implementation requirements:
- alert if `price > 1.05` or `price < 0.95`
- first transition into depeg alerts immediately
- repeated alerts while still depegged require cooldown expiry
- return to `[0.95, 1.05]` clears depeg state
- no recovery notification in v1
- message includes symbol/name, rank, current price, deviation, threshold, timestamp

Keep the depeg evaluation logic testable without needing a running async loop.

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_regressions.StablecoinDepegMonitorRegressionTests
```

Expected: PASS

**Step 5: Commit**

```bash
git add monitor/stablecoin_depeg_monitor.py tests/test_regressions.py
git commit -m "feat: add stablecoin depeg alert state machine"
```

---

### Task 4: Add the stablecoin polling loop

**Files:**
- Modify: `monitor/stablecoin_depeg_monitor.py`
- Test: `tests/test_regressions.py`

**Step 1: Write the failing tests**

Add tests that cover the polling loop behavior without relying on real sleep/network.

Add tests for:

```python
def test_stablecoin_monitor_processes_top_n_snapshots_from_client(self):
    ...


def test_stablecoin_monitor_skips_failed_poll_and_continues(self):
    ...
```

Test expectations:
- the monitor asks the client for top N rows
- malformed/exceptional poll cycles do not crash the loop
- alert-producing snapshots trigger notifier calls

Prefer testing a single-cycle helper first, then a minimal `run_once()` path, instead of trying to test an infinite loop directly.

**Step 2: Run tests to verify they fail**

Run:
```bash
python3 -m unittest tests.test_regressions.StablecoinDepegMonitorPollingTests
```

Expected: FAIL because polling entrypoints do not exist yet.

**Step 3: Write minimal implementation**

Extend `monitor/stablecoin_depeg_monitor.py` with:
- a `run_once()` method that fetches top N stablecoins and evaluates them
- a `run()` loop that repeatedly calls `run_once()` and sleeps for the configured interval
- error handling that logs and continues after a failed cycle

Implementation requirements:
- do not block process shutdown forever inside a long sleep; keep cancellation straightforward
- keep the cycle function small and testable
- do not send startup notifications from this component

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_regressions.StablecoinDepegMonitorPollingTests
```

Expected: PASS

**Step 5: Commit**

```bash
git add monitor/stablecoin_depeg_monitor.py tests/test_regressions.py
git commit -m "feat: add stablecoin depeg polling loop"
```

---

### Task 5: Wire stablecoin monitoring into monitor runtime

**Files:**
- Modify: `monitor/ws_monitor.py`
- Possibly Modify: `monitor/__init__.py`
- Test: `tests/test_regressions.py`

**Step 1: Write the failing tests**

Add integration-style regression tests that verify monitor orchestration behavior.

Add tests for:

```python
def test_ws_monitor_starts_stablecoin_task_when_enabled(self):
    ...


def test_ws_monitor_does_not_start_stablecoin_task_when_disabled(self):
    ...


def test_ws_monitor_cancels_stablecoin_task_on_shutdown(self):
    ...
```

Use patching/fake tasks consistent with the current style in `tests/test_regressions.py`.

**Step 2: Run tests to verify they fail**

Run:
```bash
python3 -m unittest tests.test_regressions.WebSocketMultiCoinMonitorStablecoinIntegrationTests
```

Expected: FAIL because runtime orchestration does not include the stablecoin task yet.

**Step 3: Write minimal implementation**

Modify `monitor/ws_monitor.py` to:
- construct the stablecoin monitor when config enables it
- start it as a background asyncio task inside `run()`
- stop/cancel it cleanly when shutdown occurs or when the monitor exits unexpectedly
- avoid changing current WebSocket monitoring behavior when stablecoin monitoring is disabled

Implementation requirements:
- preserve the existing shutdown flow and notifications
- avoid leaking background tasks
- avoid interleaving this logic into price callback paths; keep it at orchestration level

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_regressions.WebSocketMultiCoinMonitorStablecoinIntegrationTests
```

Expected: PASS

**Step 5: Commit**

```bash
git add monitor/ws_monitor.py tests/test_regressions.py monitor/stablecoin_depeg_monitor.py
 git commit -m "feat: run stablecoin depeg monitor alongside websocket monitor"
```

---

### Task 6: Document the new environment variables

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Possibly Modify: `DEPLOYMENT.md`
- Test: `tests/test_regressions.py`

**Step 1: Write the failing test**

Add a light regression test that checks `.env.example` contains the new stablecoin depeg variables.

Example:

```python
def test_env_example_includes_stablecoin_depeg_settings(self):
    content = ...
    self.assertIn("STABLECOIN_DEPEG_MONITOR_ENABLED=", content)
    self.assertIn("STABLECOIN_DEPEG_THRESHOLD_PERCENT=5", content)
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_regressions.EnvExampleRegressionTests.test_env_example_includes_stablecoin_depeg_settings
```

Expected: FAIL because docs/config example do not include the new settings yet.

**Step 3: Write minimal implementation**

Update:
- `.env.example` with the five new env vars and short comments
- `README.md` to describe:
  - what the stablecoin depeg monitor does
  - the 5% threshold behavior
  - required restart after env changes
- `DEPLOYMENT.md` only if it already documents env settings that should stay in sync

Do not add documentation beyond what is needed for operators to use the feature.

**Step 4: Run test to verify it passes**

Run:
```bash
python3 -m unittest tests.test_regressions.EnvExampleRegressionTests.test_env_example_includes_stablecoin_depeg_settings
```

Expected: PASS

**Step 5: Commit**

```bash
git add .env.example README.md DEPLOYMENT.md tests/test_regressions.py
git commit -m "docs: add stablecoin depeg monitor configuration"
```

---

### Task 7: Run the full regression suite and do final verification

**Files:**
- Verify: `tests/test_regressions.py`
- Verify: `common/config.py`
- Verify: `common/clients/defillama.py`
- Verify: `monitor/stablecoin_depeg_monitor.py`
- Verify: `monitor/ws_monitor.py`
- Verify: `.env.example`
- Verify: `README.md`

**Step 1: Run the full regression suite**

Run:
```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: PASS

**Step 2: Run a focused import smoke test**

Run:
```bash
python3 -c "import common, monitor"
```

Expected: exits 0 with no import-time errors.

**Step 3: Manually verify the feature surface**

Check that:
- stablecoin monitoring is disabled by default unless enabled in env
- enabling it does not require adding coins to `COIN_LIST`
- alert threshold is documented as ±5% around $1
- current BTC/ETH/SOL WebSocket monitoring behavior is unchanged

**Step 4: Commit**

```bash
git add common/config.py common/__init__.py common/clients/defillama.py monitor/stablecoin_depeg_monitor.py monitor/ws_monitor.py .env.example README.md DEPLOYMENT.md tests/test_regressions.py
git commit -m "feat: add stablecoin depeg monitoring"
```
