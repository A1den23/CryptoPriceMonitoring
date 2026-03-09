# Drop .env.example Change From P1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the `.env.example` cooldown example change from the `p1-reliability-fixes` worktree branch while preserving the other P1 reliability fixes.

**Architecture:** Revert only the `.env.example` volume cooldown example value to `60` and delete the regression test that enforces `.env.example` / runtime / docs alignment. Leave the runtime reliability fixes in `bot/app.py`, `common/clients/websocket.py`, and `monitor/price_monitor.py` untouched.

**Tech Stack:** Python 3.11, unittest, git worktree

---

### Task 1: Restore the example config value

**Files:**
- Modify: `.worktrees/p1-reliability-fixes/.env.example:14`

**Step 1: Write the minimal change**

Change `VOLUME_ALERT_COOLDOWN_SECONDS=5` back to `VOLUME_ALERT_COOLDOWN_SECONDS=60`.

**Step 2: Verify the file content**

Run: `git -C ".worktrees/p1-reliability-fixes" diff -- .env.example`
Expected: only the single-line example value change is reverted.

### Task 2: Remove the alignment regression test

**Files:**
- Modify: `.worktrees/p1-reliability-fixes/tests/test_regressions.py`

**Step 1: Delete the no-longer-required regression test**

Remove `EnvExampleRegressionTests` because the branch should no longer enforce `.env.example` / runtime / docs consistency.

**Step 2: Verify the test file diff**

Run: `git -C ".worktrees/p1-reliability-fixes" diff -- tests/test_regressions.py`
Expected: only the alignment test block is removed.

### Task 3: Re-run regression coverage

**Files:**
- Test: `.worktrees/p1-reliability-fixes/tests/test_regressions.py`

**Step 1: Run the full suite**

Run: `cd ".worktrees/p1-reliability-fixes" && "/home/tan81/workspace/CryptoPriceMonitoring/.venv/bin/python" -m unittest discover -s tests -p 'test_*.py'`
Expected: full suite passes.

### Task 4: Commit the adjustment

**Files:**
- Modify: `.worktrees/p1-reliability-fixes/.env.example`
- Modify: `.worktrees/p1-reliability-fixes/tests/test_regressions.py`

**Step 1: Stage only the adjustment files**

```bash
git -C ".worktrees/p1-reliability-fixes" add .env.example tests/test_regressions.py
```

**Step 2: Create a new commit**

```bash
git -C ".worktrees/p1-reliability-fixes" commit -m "$(cat <<'EOF'
chore: drop env example alignment change from p1
EOF
)"
```

**Step 3: Verify status**

Run: `git -C ".worktrees/p1-reliability-fixes" status --short`
Expected: clean working tree.
