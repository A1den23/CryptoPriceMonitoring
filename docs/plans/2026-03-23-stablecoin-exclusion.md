# Stablecoin Exclusion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Exclude `USYC` and `USDY` from both Telegram stablecoin output and background stablecoin depeg monitoring.

**Architecture:** Keep a single exclusion rule in `common/clients/defillama.py` so every consumer receives the same filtered stablecoin universe. Filter before rank assignment and top-N truncation so both `/stablecoins` and background monitoring automatically backfill with the next eligible stablecoins.

**Tech Stack:** Python 3.11, unittest, requests, Telegram bot handlers

---

### Task 1: Add regression coverage for excluded stablecoins

**Files:**
- Modify: `tests/test_regressions.py`
- Reference: `common/clients/defillama.py:38-90`
- Reference: `bot/handlers.py:105-114`
- Reference: `monitor/stablecoin_depeg_monitor.py:72-100`

**Step 1: Write the failing test**

Add a regression test near `DefiLlamaClientRegressionTests` shaped like:

```python
def test_defillama_client_excludes_usyc_and_usdy_before_top_n_ranking(self) -> None:
    payload = {
        "peggedAssets": [
            {"name": "Circle USYC", "symbol": "USYC", "price": 1.02, "circulating": 5000},
            {"name": "Ondo US Dollar Yield", "symbol": "USDY", "price": 1.01, "circulating": 4000},
            {"name": "Tether", "symbol": "USDT", "price": 1.0, "circulating": 3000},
            {"name": "USDC", "symbol": "USDC", "price": 1.0, "circulating": 2000},
            {"name": "DAI", "symbol": "DAI", "price": 1.0, "circulating": 1000},
        ]
    }

    client = DefiLlamaClient()
    snapshots = client.parse_stablecoins(payload, top_n=3)

    assert [snapshot.symbol for snapshot in snapshots] == ["USDT", "USDC", "DAI"]
    assert [snapshot.rank for snapshot in snapshots] == [1, 2, 3]
```

Requirements for this regression:
- `USYC` and `USDY` must be absent from the returned list
- returned list must still fill `top_n` using the next eligible stablecoins
- ranks must be reassigned based on the filtered list, not preserve gaps from excluded assets

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_regressions.DefiLlamaClientRegressionTests.test_defillama_client_excludes_usyc_and_usdy_before_top_n_ranking
```

Expected: FAIL because `parse_stablecoins(...)` currently includes `USYC` and `USDY`.

**Step 3: Keep the red failure focused**

If the first failure is caused by malformed test data or helper issues, fix only the test scaffolding and rerun the same command until the failure is specifically about excluded symbols still being present.

**Step 4: Re-run the failing test**

Run:
```bash
python3 -m unittest tests.test_regressions.DefiLlamaClientRegressionTests.test_defillama_client_excludes_usyc_and_usdy_before_top_n_ranking
```

Expected: FAIL only because exclusion behavior is not implemented yet.

**Step 5: Commit the failing test**

```bash
git add tests/test_regressions.py
git commit -m "test: cover stablecoin exclusions"
```

---

### Task 2: Implement shared exclusion in the DefiLlama client

**Files:**
- Modify: `common/clients/defillama.py`
- Reference: `docs/plans/2026-03-23-stablecoin-exclusion-design.md`

**Step 1: Add a fixed exclusion set**

In `common/clients/defillama.py`, add a small module-level constant or class constant for the excluded symbols:

```python
EXCLUDED_STABLECOIN_SYMBOLS = {"USYC", "USDY"}
```

Keep it fixed and local. Do not make it configurable in this task.

**Step 2: Filter before ranking**

Update `parse_stablecoins(...)` so that after validating `name`, `symbol`, `price`, and `circulating`, it skips excluded symbols before appending to the candidate snapshot list.

Target shape:

```python
if str(symbol).upper() in EXCLUDED_STABLECOIN_SYMBOLS:
    continue
```

Place this before the snapshot is appended so excluded tokens never enter sorting or rank assignment.

**Step 3: Keep rank assignment based on filtered results**

Do not change the existing sort-and-enumerate pattern except as required for the exclusion.

The final returned list should still be built from:
- eligible snapshots only
- sorted by `circulating` descending
- truncated to `top_n`
- ranks reassigned with `enumerate(..., start=1)`

**Step 4: Run the targeted exclusion regression**

Run:
```bash
python3 -m unittest tests.test_regressions.DefiLlamaClientRegressionTests.test_defillama_client_excludes_usyc_and_usdy_before_top_n_ranking
```

Expected: PASS

**Step 5: Run the full DefiLlama regression slice**

Run:
```bash
python3 -m unittest tests.test_regressions.DefiLlamaClientRegressionTests
```

Expected: PASS

**Step 6: Commit the implementation**

```bash
git add common/clients/defillama.py tests/test_regressions.py
git commit -m "feat: exclude yield-bearing stablecoins"
```

---

### Task 3: Verify downstream stablecoin consumers still work

**Files:**
- Verify: `common/clients/defillama.py`
- Verify: `bot/handlers.py`
- Verify: `monitor/stablecoin_depeg_monitor.py`
- Verify: `tests/test_regressions.py`

**Step 1: Run the Telegram stablecoin regression slice**

Run:
```bash
python3 -m unittest tests.test_regressions.TelegramBotStablecoinCommandRegressionTests
```

Expected: PASS

**Step 2: Run the stablecoin monitor regression slices**

Run:
```bash
python3 -m unittest tests.test_regressions.StablecoinDepegMonitorRegressionTests tests.test_regressions.StablecoinDepegMonitorPollingTests
```

Expected: PASS

**Step 3: Run the full regression suite**

Run:
```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: PASS

**Step 4: Sanity-check the behavioral contract**

Manually confirm from code and test results that:
- `/stablecoins` still uses `fetch_stablecoins(top_n=20)`
- background depeg monitor still uses `fetch_stablecoins(top_n=self.top_n)`
- neither call site contains duplicated `USYC`/`USDY` filtering logic
- shared filtering in the client is the only exclusion point

**Step 5: Commit the verification-only follow-up if needed**

If Task 3 required any small regression-only changes, commit them separately:

```bash
git add tests/test_regressions.py
git commit -m "test: verify stablecoin exclusion downstream"
```

If no code changed during verification, do not create an empty commit.
