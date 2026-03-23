# Stablecoin Top 25 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Increase both the background stablecoin depeg monitor and the Telegram `/stablecoins` command from the top 20 eligible stablecoins to the top 25 eligible stablecoins.

**Architecture:** Keep one shared stablecoin universe size across both paths. The background monitor continues to use the existing stablecoin config, and the Telegram command continues to query DefiLlama directly; both are updated from 20 to 25 while preserving the existing `USYC`/`USDY` exclusion rule and current rendering/error-handling structure.

**Tech Stack:** Python 3.11, asyncio, aiohttp, python-telegram-bot, unittest, Docker Compose

---

### Task 1: Update Telegram `/stablecoins` regression coverage to top 25

**Files:**
- Modify: `tests/test_regressions.py`
- Reference: `bot/handlers.py:105-114`
- Reference: `bot/messages.py:67-83`

**Step 1: Write the failing test**

Update `TelegramBotStablecoinCommandRegressionTests` so the success fixture returns 26 eligible stablecoins instead of 21, for example by changing the range to `range(2, 27)` and keeping rank 1 as `USDT`.

Update the success-path assertions to require:

```python
self.assertEqual(stablecoin_client.calls, [25])
self.assertIn("前25稳定币价格", sent_text)
ranks = [int(rank) for rank in re.findall(r"#(\d+)", sent_text)]
self.assertEqual(ranks, list(range(1, 26)))
self.assertNotIn("#26", sent_text)
self.assertNotIn("Stablecoin 26", sent_text)
```

Update the failure-path assertion to require:

```python
self.assertIn("前25稳定币价格失败", sent_text)
```

Update the help/welcome assertions to check for `/stablecoins` plus `前25稳定币价格`.

**Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_regressions.TelegramBotStablecoinCommandRegressionTests
```

Expected: FAIL because production code still fetches 20 and still renders “前20”.

**Step 3: Keep the red failure focused**

If the failure is due to fixture shape or assertion mistakes, fix only the test scaffolding and rerun the same command until the failure is specifically about `top_n=25` and/or `前25` text not being implemented yet.

**Step 4: Re-run the failing test**

Run the same command again.

Expected: FAIL only for the intended top-25 behavior mismatch.

### Task 2: Implement top-25 Telegram query behavior and copy

**Files:**
- Modify: `bot/handlers.py:105-114`
- Modify: `bot/messages.py:67-110`
- Modify: `bot/__init__.py:78-84`
- Test: `tests/test_regressions.py`

**Step 1: Write the minimal implementation**

In `bot/handlers.py`, change:

```python
stablecoins = await client.fetch_stablecoins(top_n=20)
```

to:

```python
stablecoins = await client.fetch_stablecoins(top_n=25)
```

Change the failure message to:

```python
"❌ 获取前25稳定币价格失败"
```

In `bot/messages.py`, change the stablecoin title to:

```python
message = "🪙 <b>前25稳定币价格</b>\n\n"
```

Update all stablecoin-related help and welcome text from “前20稳定币价格” to “前25稳定币价格”.

In `bot/__init__.py`, update the startup notification command list from:

```python
"/stablecoins - 查看前20稳定币价格\n"
```

to:

```python
"/stablecoins - 查看前25稳定币价格\n"
```

**Step 2: Run the targeted Telegram regression tests**

Run:

```bash
python3 -m unittest tests.test_regressions.TelegramBotStablecoinCommandRegressionTests
```

Expected: PASS.

**Step 3: Commit**

```bash
git add tests/test_regressions.py bot/handlers.py bot/messages.py bot/__init__.py
git commit -m "feat: expand Telegram stablecoin list to top 25"
```

### Task 3: Update monitor/config/documentation regressions and implementation to top 25

**Files:**
- Modify: `.env.example`
- Modify: `DEPLOYMENT.md`
- Modify: `tests/test_regressions.py`
- Reference: `monitor/stablecoin_depeg_monitor.py`
- Reference: `common/config.py`

**Step 1: Write the failing tests**

In `tests/test_regressions.py`, update all top-20 config/documentation assertions to top 25:

```python
self.assertIn("STABLECOIN_DEPEG_TOP_N=25", content)
```

Update any config fixture setup that currently hardcodes:

```python
stablecoin_depeg_top_n=20
```

to:

```python
stablecoin_depeg_top_n=25
```

Keep the exclusion regression intact; it should still prove filtering happens before ranking.

**Step 2: Run targeted tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_regressions.EnvExampleRegressionTests tests.test_regressions.StablecoinDocumentationRegressionTests tests.test_regressions.StablecoinDepegMonitorRegressionTests
```

Expected: FAIL because `.env.example` and `DEPLOYMENT.md` still say 20 and some fixtures still use 20.

**Step 3: Write the minimal implementation**

Update `.env.example` from:

```env
STABLECOIN_DEPEG_TOP_N=20
```

to:

```env
STABLECOIN_DEPEG_TOP_N=25
```

Update `DEPLOYMENT.md` stablecoin config examples from 20 to 25.

If any regression fixture still uses 20 only because of old scope assumptions, update those fixture values to 25.

Do not add new config fields or change threshold/cooldown semantics.

**Step 4: Run the targeted tests again**

Run the same command:

```bash
python3 -m unittest tests.test_regressions.EnvExampleRegressionTests tests.test_regressions.StablecoinDocumentationRegressionTests tests.test_regressions.StablecoinDepegMonitorRegressionTests
```

Expected: PASS.

**Step 5: Commit**

```bash
git add .env.example DEPLOYMENT.md tests/test_regressions.py
git commit -m "feat: expand stablecoin monitor scope to top 25"
```

### Task 4: Run full verification and Docker rebuild validation

**Files:**
- Reference: `docker-compose.yml`
- Reference: `common/clients/defillama.py`
- Reference: `tests/test_regressions.py`

**Step 1: Run the full regression suite**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: PASS.

**Step 2: Rebuild and restart Docker services**

Run:

```bash
docker compose up -d --build crypto-monitor crypto-bot
```

Expected: images rebuilt, containers recreated successfully.

**Step 3: Verify service health and runtime behavior**

Run:

```bash
docker compose ps
```

Expected: both `crypto-monitor` and `crypto-bot` are `Up` and healthy.

Then run:

```bash
docker exec crypto-monitor python -c "from common.clients.defillama import DefiLlamaClient; payload={'peggedAssets':[{'name':'Circle USYC','symbol':'USYC','price':1.02,'circulating':{'peggedUSD':5000}},{'name':'Ondo US Dollar Yield','symbol':'USDY','price':1.01,'circulating':{'peggedUSD':4000}},{'name':'Tether','symbol':'USDT','price':1.0,'circulating':{'peggedUSD':3000}},{'name':'USDC','symbol':'USDC','price':1.0,'circulating':{'peggedUSD':2000}},{'name':'DAI','symbol':'DAI','price':1.0,'circulating':{'peggedUSD':1000}}]}; snapshots=DefiLlamaClient().parse_stablecoins(payload, top_n=3); print([s.symbol for s in snapshots]); print([s.rank for s in snapshots])"
```

Expected:

```text
['USDT', 'USDC', 'DAI']
[1, 2, 3]
```

**Step 4: Check logs**

Run:

```bash
docker compose logs --tail=50 crypto-monitor crypto-bot
```

Verify from logs that:
- both services restarted cleanly
- stablecoin polling still runs
- no immediate evidence shows `USYC` or `USDY` alerts after the rebuild

**Step 5: Commit**

```bash
git add .
git commit -m "test: verify stablecoin top 25 rollout"
```
