# Startup Stablecoin Refresh Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Docker startup automatically generate the stablecoin universe cache once when the cache file does not exist, so fresh environments do not require a manual pre-run refresh.

**Architecture:** Add a small shared startup wrapper that both Docker services use before launching their main Python module. The wrapper checks `STABLECOIN_UNIVERSE_CACHE_PATH`, runs `python -m common.stablecoin_universe refresh` only when the file is missing, then `exec`s the requested service command unchanged. Keep the existing daily cron/manual refresh workflow for ongoing updates.

**Tech Stack:** Python 3.11, Docker, Docker Compose, POSIX shell, unittest

---

### Task 1: Add failing deployment-contract tests for startup bootstrap

**Files:**
- Modify: `tests/test_deployment_contracts.py`
- Modify: `tests/test_entrypoints.py`

**Step 1: Write the failing test**

Add contract assertions proving the new startup behavior is represented in deployment files.

Example assertions to add:

```python
self.assertIn("docker/start-service.sh", dockerfile)
self.assertIn('ENTRYPOINT ["/app/docker/start-service.sh"]', dockerfile)
self.assertIn('["python", "-m", "monitor"]', compose)
self.assertIn('["python", "-m", "bot"]', compose)
self.assertIn("STABLECOIN_UNIVERSE_CACHE_PATH", readme)
self.assertRegex(readme, r"缓存不存在[\s\S]{0,80}自动")
```

Also extend the entrypoint contract test so runtime/docs still agree after introducing the shared entrypoint wrapper.

**Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_deployment_contracts tests.test_entrypoints -v
```

Expected: FAIL because Dockerfile and docs do not yet mention the startup bootstrap wrapper.

**Step 3: Write minimal implementation**

Update the tests only. Do not change runtime files yet.

**Step 4: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_deployment_contracts tests.test_entrypoints -v
```

Expected: FAIL with missing Dockerfile / docs assertions.

**Step 5: Commit**

```bash
git add tests/test_deployment_contracts.py tests/test_entrypoints.py
git commit -m "test: cover startup stablecoin cache bootstrap contract"
```

### Task 2: Add a failing runtime-bootstrap test for missing-cache behavior

**Files:**
- Create: `tests/test_startup_bootstrap.py`
- Read for reference: `common/stablecoin_universe.py`

**Step 1: Write the failing test**

Create focused unit tests for the bootstrap logic with subprocess calls mocked.

Cover at least these cases:

```python
def test_bootstrap_refreshes_when_cache_missing(self): ...
def test_bootstrap_skips_refresh_when_cache_exists(self): ...
def test_bootstrap_executes_requested_command_after_refresh_check(self): ...
def test_bootstrap_uses_configured_cache_path(self): ...
```

Model the bootstrap API around a pure function that receives:
- cache path
- argv for the target command
- collaborators for `exists`, `refresh`, and `exec`

This keeps tests deterministic and avoids shell-heavy testing.

**Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_startup_bootstrap -v
```

Expected: FAIL because the bootstrap module does not exist yet.

**Step 3: Write minimal implementation**

Only create the test file and imports needed for the failing state.

**Step 4: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_startup_bootstrap -v
```

Expected: FAIL with import error or missing symbol error.

**Step 5: Commit**

```bash
git add tests/test_startup_bootstrap.py
git commit -m "test: add startup bootstrap regression coverage"
```

### Task 3: Implement a small Python bootstrap module for startup cache initialization

**Files:**
- Create: `common/startup.py`
- Test: `tests/test_startup_bootstrap.py`

**Step 1: Write the failing test**

If needed, refine the Task 2 tests so they assert exact behavior before implementing.

Reference behavior to lock down:

```python
refresh_calls == [cache_path]  # only when file missing
exec_calls == [["python", "-m", "monitor"]]
```

**Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_startup_bootstrap -v
```

Expected: FAIL because `common.startup` does not yet implement the tested API.

**Step 3: Write minimal implementation**

Implement a tiny bootstrap module with functions like:

```python
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def configured_cache_path(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get("STABLECOIN_UNIVERSE_CACHE_PATH", "data/stablecoin_top25.json"))


def ensure_stablecoin_cache(
    cache_path: Path,
    run_refresh,
) -> None:
    if cache_path.exists():
        return
    run_refresh()


def run_refresh_command() -> None:
    subprocess.run(
        [sys.executable, "-m", "common.stablecoin_universe", "refresh"],
        check=True,
    )


def bootstrap(argv: list[str]) -> int:
    if not argv:
        raise SystemExit("Usage: python -m common.startup <command> [args...]")
    ensure_stablecoin_cache(configured_cache_path(), run_refresh_command)
    os.execvp(argv[0], argv)
```

Keep it minimal. No lazy runtime fallback. No retries.

**Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest tests.test_startup_bootstrap -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add common/startup.py tests/test_startup_bootstrap.py
git commit -m "feat: bootstrap stablecoin cache on startup"
```

### Task 4: Wire Docker image startup through the bootstrap module

**Files:**
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Test: `tests/test_deployment_contracts.py`
- Test: `tests/test_entrypoints.py`

**Step 1: Write the failing test**

If needed, tighten contract assertions before changing runtime files.

Target contract:

```python
self.assertIn('CMD ["python", "-m", "common.startup", "python", "-m", "monitor"]', dockerfile)
self.assertIn('["python", "-m", "common.startup", "python", "-m", "monitor"]', compose)
self.assertIn('["python", "-m", "common.startup", "python", "-m", "bot"]', compose)
```

Prefer one consistent mechanism in both Dockerfile and Compose. Do not mix shell-form commands.

**Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_deployment_contracts tests.test_entrypoints -v
```

Expected: FAIL because runtime files still point directly to `python -m monitor` / `python -m bot`.

**Step 3: Write minimal implementation**

Update runtime entrypoints so both services start through the bootstrap module while preserving the final target process:

```dockerfile
CMD ["python", "-m", "common.startup", "python", "-m", "monitor"]
```

```yaml
command: ["python", "-m", "common.startup", "python", "-m", "monitor"]
command: ["python", "-m", "common.startup", "python", "-m", "bot"]
```

Also ensure the `common/` package copy already includes the new module.

**Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest tests.test_deployment_contracts tests.test_entrypoints -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml tests/test_deployment_contracts.py tests/test_entrypoints.py
git commit -m "feat: route docker startup through cache bootstrap"
```

### Task 5: Update docs for automatic first-run cache initialization

**Files:**
- Modify: `README.md`
- Modify: `DEPLOYMENT.md`
- Test: `tests/test_deployment_contracts.py`
- Test: `tests/test_regressions.py`

**Step 1: Write the failing test**

Add assertions proving the docs explain both behaviors:
- first startup auto-generates the cache when missing
- daily refresh command/cron still exists

Example assertions:

```python
self.assertIn("首次启动", readme)
self.assertRegex(readme, r"缓存.*不存在.*自动.*refresh")
self.assertIn("python -m common.stablecoin_universe refresh", readme)
self.assertIn("0 2 * * *", readme)
```

Mirror the same idea in `DEPLOYMENT.md` coverage.

**Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_deployment_contracts tests.test_regressions -v
```

Expected: FAIL because current docs require a manual first-run refresh.

**Step 3: Write minimal implementation**

Update docs so they say:
- `docker compose up -d --build` will auto-refresh once if cache is absent
- manual refresh remains available
- daily cron remains recommended for ongoing updates
- `/stablecoins` and the depeg monitor still share `/app/data/stablecoin_top25.json`

Do not document refresh-on-every-start.

**Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest tests.test_deployment_contracts tests.test_regressions -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add README.md DEPLOYMENT.md tests/test_deployment_contracts.py tests/test_regressions.py
git commit -m "docs: describe automatic startup stablecoin refresh"
```

### Task 6: Run focused verification for code and docs

**Files:**
- Test: `tests/test_startup_bootstrap.py`
- Test: `tests/test_deployment_contracts.py`
- Test: `tests/test_entrypoints.py`
- Test: `tests/test_regressions.py`

**Step 1: Run the focused suite**

Run:

```bash
python3 -m unittest \
  tests.test_startup_bootstrap \
  tests.test_deployment_contracts \
  tests.test_entrypoints \
  tests.test_regressions -v
```

Expected: PASS.

**Step 2: Fix only failures directly related to this feature**

If any fail, make the smallest change necessary and rerun the same command.

**Step 3: Commit**

```bash
git add common/startup.py Dockerfile docker-compose.yml README.md DEPLOYMENT.md \
  tests/test_startup_bootstrap.py tests/test_deployment_contracts.py \
  tests/test_entrypoints.py tests/test_regressions.py
git commit -m "test: verify startup stablecoin refresh flow"
```

### Task 7: Run end-to-end Docker verification in a fresh-cache scenario

**Files:**
- Runtime verification only

**Step 1: Remove only the shared cache volume for this project**

Run:

```bash
docker compose down
docker volume rm cryptopricemonitoring_stablecoin-cache
```

Expected: the next startup has no preexisting cache.

**Step 2: Start services fresh**

Run:

```bash
docker compose up -d --build
```

Expected: services start successfully.

**Step 3: Verify logs show one-time bootstrap refresh**

Run:

```bash
docker compose logs --tail=200 crypto-monitor
docker compose logs --tail=200 crypto-bot
```

Expected: at least one service logs the missing-cache bootstrap refresh path; no repeated refresh loop on restart.

**Step 4: Verify cache file exists in shared volume**

Run:

```bash
docker compose exec crypto-bot ls -l /app/data
docker compose exec crypto-monitor ls -l /app/data
```

Expected: `/app/data/stablecoin_top25.json` exists.

**Step 5: Verify services are healthy**

Run:

```bash
docker compose ps
```

Expected: `crypto-monitor` and `crypto-bot` are `Up` and healthy.

**Step 6: Commit**

```bash
git add common/startup.py Dockerfile docker-compose.yml README.md DEPLOYMENT.md \
  tests/test_startup_bootstrap.py tests/test_deployment_contracts.py \
  tests/test_entrypoints.py tests/test_regressions.py
git commit -m "feat: auto-bootstrap stablecoin cache on first startup"
```
