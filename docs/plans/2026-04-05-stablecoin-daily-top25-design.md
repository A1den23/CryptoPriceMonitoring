# Stablecoin Daily Top 25 Refresh Design

**Date:** 2026-04-05
**Status:** Approved

## Goal

Make Telegram `/stablecoins` and the background stablecoin depeg monitor always use the same daily-refreshed top-25 stablecoin universe from DefiLlama.

The daily refresh runs at 02:00 Beijing time.

## Chosen Approach

Use a single shared local cache file as the source of truth for the stablecoin universe.

A dedicated refresh command fetches the eligible top 25 stablecoins from DefiLlama once per day and atomically writes the result to the cache file. A system cron job triggers that refresh at 02:00 Beijing time.

Both `/stablecoins` and the depeg monitor read from that cache instead of independently deciding the top-25 set.

## Why This Approach

- It is the simplest way to guarantee both consumers use the exact same list.
- It avoids divergence between the bot and monitor, which are separate runtime entrypoints.
- It keeps restart behavior predictable because the current day’s universe survives process restarts.
- It makes failures easier to reason about: refresh failures are isolated to one job and do not silently create two different universes.

## Scope

This change adds:

- a persistent stablecoin universe cache file
- a one-shot refresh command that fetches and writes the daily top 25
- cron-driven daily refresh at 02:00 Beijing time
- `/stablecoins` reading from the shared cache
- stablecoin depeg monitoring using the cached symbol universe
- tests covering cache refresh, cache read, and failure behavior

This change does not add:

- per-consumer stablecoin universes
- background self-refresh logic in both bot and monitor processes
- fallback to live DefiLlama fetches when the cache is missing or invalid
- multiple refresh windows per day

## Architecture

### Shared universe cache

Add a local JSON cache file that stores:

- refresh timestamp
- configured universe size (`25`)
- stablecoin entries returned after the existing DefiLlama filtering rules

Each cached entry should include the fields already used downstream today:

- `name`
- `symbol`
- `price`
- `circulating`
- `rank`

The DefiLlama client remains the source for filtering and ranking logic, including the existing exclusions for `USYC` and `USDY`.

### Refresh command

Add a dedicated one-shot refresh path that:

1. fetches the top 25 eligible stablecoins from DefiLlama
2. serializes the result into the cache format
3. writes the file atomically so readers never observe a partial file

If refresh fails, the command must leave the previous successful cache untouched.

### Telegram `/stablecoins`

The `/stablecoins` command currently fetches live data directly from DefiLlama in [bot/handlers.py:112-118](bot/handlers.py#L112-L118).

After this change, `/stablecoins` reads the shared cache and renders that cached list.

This means the command shows the current day’s approved universe rather than recomputing the universe on each invocation.

### Stablecoin depeg monitor

The stablecoin depeg monitor currently fetches the top-N set directly each polling cycle in [monitor/stablecoin_depeg_monitor.py:94-96](monitor/stablecoin_depeg_monitor.py#L94-L96).

After this change, the monitor still fetches current stablecoin market data from DefiLlama during polling, but it no longer uses a fresh top-N cutoff each time. Instead, it filters the fetched snapshots down to the symbol set stored in the daily cache.

This preserves current polling semantics for prices and alert timing while making the monitored universe stable for the whole day.

### Scheduling

Use a system cron job to execute the refresh command every day at 02:00 Beijing time.

If the host uses Asia/Shanghai local time, cron can schedule directly at 02:00. If not, deployment instructions must state the required timezone handling.

## Behavior

- `/stablecoins` and the depeg monitor use the same daily universe
- the universe changes only after the daily refresh job succeeds
- the depeg monitor still evaluates current prices on its normal polling interval
- `USYC` and `USDY` remain excluded because filtering still happens in the DefiLlama client layer
- if fewer than 25 eligible stablecoins are available, the cache stores whatever eligible set is returned

## Error Handling

- refresh failure does not overwrite the previous cache
- missing cache is a hard error for `/stablecoins`
- missing cache is a hard error for stablecoin monitor startup or polling path, with explicit logging
- invalid or corrupted cache is treated the same as missing cache
- there is no fallback to live top-25 fetches because that would break the single-shared-universe guarantee

## Testing Strategy

Add or update tests to prove:

- the refresh command writes the expected cache payload from DefiLlama snapshots
- refresh writes are atomic and do not replace the previous cache on failure
- `/stablecoins` renders from cached data instead of live top-25 fetches
- the depeg monitor evaluates only symbols present in the shared cache
- missing or invalid cache produces the expected failure path
- the existing exclusion behavior for `USYC` and `USDY` still applies to the cached universe

## Success Criteria

This work is complete when:

- a daily job refreshes the stablecoin universe at 02:00 Beijing time
- `/stablecoins` always uses the cached daily universe
- the depeg monitor always evaluates the same cached daily universe
- bot and monitor no longer independently determine top-25 membership
- refresh failures preserve the last successful universe
- cache absence or corruption fails loudly instead of silently diverging behavior
