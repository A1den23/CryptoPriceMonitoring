# Stablecoin Daily Top 25 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `/stablecoins` and the stablecoin depeg monitor use the same daily-refreshed DefiLlama top-25 stablecoin universe, refreshed at 02:00 Beijing time.

**Architecture:** Add one shared stablecoin-universe cache file and one refresh command that writes it atomically. Both bot and monitor will stop deciding top-25 membership independently; instead they will load the cached universe and fetch live DefiLlama snapshots only for current pricing/depeg evaluation. In Docker, mount the cache on a shared named volume so both containers read the same file, and document a host cron job that runs the refresh command once per day.

**Tech Stack:** Python 3.11, asyncio, aiohttp, json, pathlib, unittest, Docker Compose, cron

---

### Task 1: Add stablecoin universe cache tests and module skeleton

**Files:**
- Create: `common/stablecoin_universe.py`
- Create: `tests/test_stablecoin_universe.py`
- Modify: `common/config.py`

**Step 1: Write the failing tests**

Create `tests/test_stablecoin_universe.py` with focused tests for:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
import types
import unittest

from common.clients.defillama import StablecoinSnapshot
from common.stablecoin_universe import (
    load_cached_stablecoin_universe,
    refresh_stablecoin_universe,
)


class StablecoinUniverseCacheTests(unittest.TestCase):
    def test_refresh_writes_cache_file(self) -> None:
        snapshots = [StablecoinSnapshot("Tether", "USDT", 1.0, 100.0, 1)]
        client = types.SimpleNamespace(fetch_stablecoins=AsyncMock(return_value=snapshots))
        ...
        cached = load_cached_stablecoin_universe(cache_path)
        self.assertEqual([item.symbol for item in cached.snapshots], ["USDT"])

    def test_refresh_failure_keeps_previous_cache_file(self) -> None:
        ...

    def test_load_cached_stablecoin_universe_raises_for_missing_file(self) -> None:
        with self.assertRaises(ValueError):
            load_cached_stablecoin_universe(missing_path)
```

In `common/config.py`, add a config regression in the existing config tests area for:

```python
self.assertEqual(config.stablecoin_universe_cache_path, "data/stablecoin_top25.json")
```

and an override case such as:

```python
{"STABLECOIN_UNIVERSE_CACHE_PATH": "/app/data/custom-top25.json"}
```

**Step 2: Run the targeted tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_stablecoin_universe
```

Expected: FAIL because `common.stablecoin_universe` does not exist yet.

**Step 3: Add the minimal module and config wiring**

In `common/stablecoin_universe.py`, add:

```python
from dataclasses import dataclass
from pathlib import Path
import json
import tempfile

from common.clients.defillama import StablecoinSnapshot


@dataclass(frozen=True, slots=True)
class CachedStablecoinUniverse:
    refreshed_at: str
    top_n: int
    snapshots: list[StablecoinSnapshot]
```

Implement:
- `load_cached_stablecoin_universe(cache_path: str | Path) -> CachedStablecoinUniverse`
- `write_cached_stablecoin_universe(...) -> None`
- `async def refresh_stablecoin_universe(client, cache_path: str | Path, top_n: int = 25) -> CachedStablecoinUniverse`

Use a temp file in the same directory plus `Path.replace(...)` for atomic writes.

In `common/config.py`, add:

```python
self.stablecoin_universe_cache_path = os.getenv(
    "STABLECOIN_UNIVERSE_CACHE_PATH",
    "data/stablecoin_top25.json",
)
```

Do not add more config than needed.

**Step 4: Re-run the targeted tests**

Run:

```bash
python3 -m unittest tests.test_stablecoin_universe
```

Expected: PASS.

**Step 5: Commit**

```bash
git add common/stablecoin_universe.py common/config.py tests/test_stablecoin_universe.py tests/test_regressions.py
git commit -m "feat: add stablecoin universe cache support"
```

### Task 2: Add live snapshot resolution for the cached universe

**Files:**
- Modify: `common/clients/defillama.py`
- Modify: `common/stablecoin_universe.py`
- Modify: `tests/test_regressions.py`

**Step 1: Write the failing tests**

In `tests/test_regressions.py`, extend the DefiLlama/client regression area with tests for:

```python
def test_fetch_all_stablecoins_returns_all_ranked_snapshots(self) -> None:
    client = DefiLlamaClient()
    payload = {
        "peggedAssets": [
            {"name": "USDT", "symbol": "USDT", "price": 1.0, "circulating": {"peggedUSD": 3}},
            {"name": "USDC", "symbol": "USDC", "price": 1.0, "circulating": {"peggedUSD": 2}},
        ]
    }
    snapshots = client.parse_stablecoins(payload, top_n=None)
    self.assertEqual([s.symbol for s in snapshots], ["USDT", "USDC"])
```

and for the shared resolver:

```python
async def test_resolve_live_snapshots_for_cached_universe_preserves_cached_rank_order():
    cached = [
        StablecoinSnapshot("USDC", "USDC", 1.0, 100.0, 2),
        StablecoinSnapshot("USDT", "USDT", 1.0, 200.0, 1),
    ]
    live = [
        StablecoinSnapshot("USDT", "USDT", 0.999, 210.0, 1),
        StablecoinSnapshot("USDC", "USDC", 1.001, 110.0, 2),
    ]
    ...
    self.assertEqual([s.rank for s in resolved], [2, 1])
    self.assertEqual([s.price for s in resolved], [1.001, 0.999])
```

Use `IsolatedAsyncioTestCase` for async helper tests if needed.

**Step 2: Run the targeted tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_regressions.DefiLlamaClientRegressionTests tests.test_stablecoin_universe
```

Expected: FAIL because `top_n=None` and the live cached-universe resolver are not implemented yet.

**Step 3: Write the minimal implementation**

In `common/clients/defillama.py`, change the parser signature to:

```python
def parse_stablecoins(self, payload: dict, top_n: int | None) -> list[StablecoinSnapshot]:
```

and only slice when `top_n is not None`:

```python
selected = snapshots if top_n is None else snapshots[:top_n]
```

Add:

```python
async def fetch_all_stablecoins(self) -> list[StablecoinSnapshot]:
    session = await self._get_session()
    async with session.get(f"{self.base_url}/stablecoins") as response:
        response.raise_for_status()
        payload = await response.json()
        return self.parse_stablecoins(payload, top_n=None)
```

In `common/stablecoin_universe.py`, add:

```python
async def resolve_live_snapshots_for_cached_universe(client, cache_path: str | Path) -> list[StablecoinSnapshot]:
    cached = load_cached_stablecoin_universe(cache_path)
    live = await client.fetch_all_stablecoins()
    live_by_symbol = {snapshot.symbol: snapshot for snapshot in live}
    resolved = []
    for cached_snapshot in cached.snapshots:
        live_snapshot = live_by_symbol.get(cached_snapshot.symbol)
        if live_snapshot is None:
            continue
        resolved.append(
            StablecoinSnapshot(
                name=live_snapshot.name,
                symbol=live_snapshot.symbol,
                price=live_snapshot.price,
                circulating=live_snapshot.circulating,
                rank=cached_snapshot.rank,
            )
        )
    return resolved
```

Keep the cache as the source of truth for membership and rank.

**Step 4: Re-run the targeted tests**

Run:

```bash
python3 -m unittest tests.test_regressions.DefiLlamaClientRegressionTests tests.test_stablecoin_universe
```

Expected: PASS.

**Step 5: Commit**

```bash
git add common/clients/defillama.py common/stablecoin_universe.py tests/test_regressions.py tests/test_stablecoin_universe.py
git commit -m "feat: resolve live snapshots from cached stablecoin universe"
```

### Task 3: Switch `/stablecoins` to the shared cached universe

**Files:**
- Modify: `bot/handlers.py`
- Modify: `tests/test_regressions.py`
- Reference: `bot/messages.py`

**Step 1: Write the failing tests**

Update `TelegramBotStablecoinCommandRegressionTests` in `tests/test_regressions.py` so the command no longer asserts direct `fetch_stablecoins(top_n=25)` calls.

Replace the success-path expectation with a shared-cache flow such as:

```python
stablecoin_client = FakeStablecoinClient(all_snapshots=self._build_snapshots())
with (
    patch("bot.handlers.DefiLlamaClient", return_value=stablecoin_client, create=True),
    patch("bot.handlers.resolve_live_snapshots_for_cached_universe", new=AsyncMock(return_value=self._build_snapshots()[:25])),
):
    asyncio.run(telegram_bot.stablecoins_command(update, context))

self.assertEqual(stablecoin_client.calls, [])
self.assertIn("前25稳定币价格", sent_text)
```

Add an explicit cache-missing failure-path test:

```python
with patch(
    "bot.handlers.resolve_live_snapshots_for_cached_universe",
    new=AsyncMock(side_effect=ValueError("missing cache")),
):
    asyncio.run(telegram_bot.stablecoins_command(update, context))
self.assertIn("前25稳定币价格失败", sent_text)
```

**Step 2: Run the targeted tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_regressions.TelegramBotStablecoinCommandRegressionTests
```

Expected: FAIL because `bot/handlers.py` still fetches the top 25 directly.

**Step 3: Write the minimal implementation**

In `bot/handlers.py`, replace the direct fetch block:

```python
async with DefiLlamaClient() as client:
    stablecoins = await client.fetch_stablecoins(top_n=25)
```

with:

```python
async with DefiLlamaClient() as client:
    stablecoins = await resolve_live_snapshots_for_cached_universe(
        client,
        self.config.stablecoin_universe_cache_path,
    )
```

Import only the new shared helper needed from `common.stablecoin_universe`.

Keep the existing success rendering and existing concise failure message.

**Step 4: Re-run the targeted tests**

Run:

```bash
python3 -m unittest tests.test_regressions.TelegramBotStablecoinCommandRegressionTests
```

Expected: PASS.

**Step 5: Commit**

```bash
git add bot/handlers.py tests/test_regressions.py
git commit -m "feat: drive stablecoins command from cached universe"
```

### Task 4: Switch the depeg monitor to the shared cached universe

**Files:**
- Modify: `monitor/stablecoin_depeg_monitor.py`
- Modify: `tests/test_stablecoin_monitor.py`
- Modify: `tests/test_regressions.py`
- Reference: `monitor/ws_monitor.py`

**Step 1: Write the failing tests**

In `tests/test_stablecoin_monitor.py`, stop asserting `fetch_stablecoins(top_n=...)` and instead assert cache-backed live resolution.

Add a polling-path test like:

```python
@patch("monitor.stablecoin_depeg_monitor.resolve_live_snapshots_for_cached_universe", new_callable=AsyncMock)
def test_run_once_processes_only_cached_universe_snapshots(self, mock_resolve) -> None:
    mock_resolve.return_value = [
        StablecoinSnapshot("USDC", "USDC", 0.94, 1000.0, 2),
        StablecoinSnapshot("DAI", "DAI", 0.93, 500.0, 3),
    ]
    alerts = asyncio.run(stablecoin_monitor.run_once())
    self.assertEqual(alerts, 2)
```

Add a cache-missing error-path regression in `tests/test_regressions.py` that verifies `run_once()` raises `ValueError` and `run()` logs the failure then continues to the next sleep cycle.

**Step 2: Run the targeted tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_stablecoin_monitor tests.test_regressions.StablecoinDepegMonitorPollingTests tests.test_regressions.StablecoinDepegMonitorAsyncPollingTests
```

Expected: FAIL because the monitor still asks the client for `top_n` snapshots directly.

**Step 3: Write the minimal implementation**

In `monitor/stablecoin_depeg_monitor.py`, replace:

```python
self.top_n = config.stablecoin_depeg_top_n
...
snapshots = await self.client.fetch_stablecoins(top_n=self.top_n)
```

with:

```python
self.cache_path = config.stablecoin_universe_cache_path
...
snapshots = await resolve_live_snapshots_for_cached_universe(
    self.client,
    self.cache_path,
)
```

Do not add a second stablecoin-universe decision path.

Keep the existing threshold, cooldown, async send path, and log summary structure.

**Step 4: Re-run the targeted tests**

Run:

```bash
python3 -m unittest tests.test_stablecoin_monitor tests.test_regressions.StablecoinDepegMonitorPollingTests tests.test_regressions.StablecoinDepegMonitorAsyncPollingTests
```

Expected: PASS.

**Step 5: Commit**

```bash
git add monitor/stablecoin_depeg_monitor.py tests/test_stablecoin_monitor.py tests/test_regressions.py
git commit -m "feat: drive stablecoin monitor from cached universe"
```

### Task 5: Add refresh CLI wiring, shared Docker volume, and deployment docs

**Files:**
- Modify: `common/stablecoin_universe.py`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `DEPLOYMENT.md`
- Modify: `tests/test_deployment_contracts.py`
- Modify: `tests/test_regressions.py`

**Step 1: Write the failing tests**

In `tests/test_deployment_contracts.py`, add assertions for:

```python
self.assertIn("STABLECOIN_UNIVERSE_CACHE_PATH", deployment)
self.assertIn("python -m common.stablecoin_universe refresh", deployment)
self.assertRegex(deployment, r"0 2 \* \* \*")
```

Add a docs assertion for the shared cache volume or cache path in `README.md` as well.

In `tests/test_regressions.py`, add env-example assertions such as:

```python
self.assertIn("STABLECOIN_UNIVERSE_CACHE_PATH=/app/data/stablecoin_top25.json", content)
```

**Step 2: Run the targeted tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_deployment_contracts tests.test_regressions.EnvExampleRegressionTests tests.test_regressions.StablecoinDocumentationRegressionTests
```

Expected: FAIL because the cache path, refresh command, and cron docs are not documented yet.

**Step 3: Write the minimal implementation**

In `common/stablecoin_universe.py`, add a module CLI:

```python
def main() -> None:
    load_environment()
    config = ConfigManager()
    if len(sys.argv) != 2 or sys.argv[1] != "refresh":
        raise SystemExit("Usage: python -m common.stablecoin_universe refresh")
    asyncio.run(_refresh_from_config(config))
```

In `Dockerfile`, ensure `/app/data` exists and is owned by `appuser`:

```dockerfile
RUN mkdir -p /app/logs /app/data && chown -R appuser:appuser /app
```

In `docker-compose.yml`, mount a shared named volume into both services:

```yaml
volumes:
  - crypto-logs:/app/logs
  - stablecoin-cache:/app/data
```

and define:

```yaml
volumes:
  crypto-logs:
  stablecoin-cache:
```

In `.env.example`, add:

```env
STABLECOIN_UNIVERSE_CACHE_PATH=/app/data/stablecoin_top25.json
```

In `DEPLOYMENT.md`, document host cron using Beijing time with a command like:

```cron
0 2 * * * cd /path/to/CryptoPriceMonitoring && /usr/bin/docker compose run --rm crypto-monitor python -m common.stablecoin_universe refresh >> logs/stablecoin-refresh.log 2>&1
```

Also document that both containers must share `/app/data` via the `stablecoin-cache` volume.

Update `README.md` only as far as needed to keep runtime instructions accurate.

**Step 4: Re-run the targeted tests**

Run:

```bash
python3 -m unittest tests.test_deployment_contracts tests.test_regressions.EnvExampleRegressionTests tests.test_regressions.StablecoinDocumentationRegressionTests
```

Expected: PASS.

**Step 5: Commit**

```bash
git add common/stablecoin_universe.py Dockerfile docker-compose.yml .env.example README.md DEPLOYMENT.md tests/test_deployment_contracts.py tests/test_regressions.py
git commit -m "feat: add daily stablecoin universe refresh workflow"
```

### Task 6: Run end-to-end verification

**Files:**
- Reference: `common/stablecoin_universe.py`
- Reference: `bot/handlers.py`
- Reference: `monitor/stablecoin_depeg_monitor.py`
- Reference: `docker-compose.yml`

**Step 1: Run the focused Python test suite**

Run:

```bash
python3 -m unittest tests.test_stablecoin_universe tests.test_stablecoin_monitor tests.test_regressions tests.test_deployment_contracts tests.test_entrypoints
```

Expected: PASS.

**Step 2: Run the full suite**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: PASS.

**Step 3: Build the containers**

Run:

```bash
docker compose build crypto-monitor crypto-bot
```

Expected: both images build successfully with the new shared cache volume path.

**Step 4: Exercise the refresh command in Docker**

Run:

```bash
docker compose run --rm crypto-monitor python -m common.stablecoin_universe refresh
```

Expected: command exits 0 and writes `/app/data/stablecoin_top25.json` into the shared `stablecoin-cache` volume.

Then verify the file exists from both service definitions:

```bash
docker compose run --rm crypto-monitor python -c "from pathlib import Path; p=Path('/app/data/stablecoin_top25.json'); print(p.exists()); print(p.read_text()[:120])"
docker compose run --rm crypto-bot python -c "from pathlib import Path; print(Path('/app/data/stablecoin_top25.json').exists())"
```

Expected:
- first command prints `True`
- second command prints `True`

**Step 5: Start the services and inspect logs**

Run:

```bash
docker compose up -d crypto-monitor crypto-bot
docker compose ps
docker compose logs --tail=80 crypto-monitor crypto-bot
```

Expected:
- both services are `Up`
- bot starts without stablecoin cache lookup errors
- monitor stablecoin polling runs without cache lookup errors

**Step 6: Commit**

```bash
git add .
git commit -m "test: verify cached stablecoin universe rollout"
```
