# Stablecoin Top 25 Design

**Date:** 2026-03-23
**Status:** Approved

## Goal

Increase the stablecoin scope from top 20 to top 25 for both the background depeg monitor and the Telegram `/stablecoins` command.

## Chosen Approach

Keep a single shared stablecoin universe size across monitoring and Telegram query paths, and change that shared scope from 20 to 25.

## Why This Approach

- It keeps the operator-facing Telegram output aligned with the backend monitoring scope.
- It avoids introducing separate limits or new configuration knobs for a simple scope increase.
- It preserves the existing architecture where both paths consume the same filtered DefiLlama result set.
- It keeps the change small: mostly constant, copy, config, and regression updates.

## Scope

This change adds:

- top-25 monitoring for the stablecoin depeg monitor
- top-25 output for the Telegram `/stablecoins` command
- text updates from “前20” to “前25” where the stablecoin list is described
- regression updates covering the new limit

This change does not add:

- a separate Telegram-only stablecoin limit
- pagination or chunked Telegram responses
- a new environment variable for Telegram stablecoin count
- any changes to the excluded-symbol rule for `USYC` and `USDY`

## Architecture

### Shared stablecoin scope

The DefiLlama client remains the shared source for stablecoin ranking and filtering.

The existing exclusion rule for `USYC` and `USDY` still applies before ranking and truncation.

After exclusions, both monitoring and Telegram query paths will operate on the top 25 eligible stablecoins.

### Background monitor

The background stablecoin depeg monitor continues to use `stablecoin_depeg_top_n`, but its documented and default scope becomes 25.

This expands the monitoring surface by 5 additional eligible stablecoins without changing the alerting model, polling cadence, or cooldown semantics.

### Telegram command

The `/stablecoins` command in `bot/handlers.py` changes from requesting 20 snapshots to 25 snapshots.

Message rendering in `bot/messages.py` continues to render the returned list directly, but the title and related copy are updated to say “前25稳定币价格”.

## Behavior

- `/stablecoins` returns the latest 25 eligible stablecoins
- background depeg monitoring evaluates the latest 25 eligible stablecoins
- `USYC` and `USDY` remain excluded from both paths
- rank numbering remains based on the filtered result set
- if fewer than 25 eligible stablecoins are available, the existing rendering behavior continues to show what is returned

## Error Handling

- No new error-handling paths are introduced
- Existing DefiLlama fetch and parse failures still surface through the current concise Telegram error message and monitor logging
- Only the limit and user-facing copy change

## Testing Strategy

Update regression coverage to prove:

- `/stablecoins` requests 25 stablecoins instead of 20
- `/stablecoins` renders ranks `#1` through `#25`
- rank `#26` is not shown
- help, welcome, startup, and failure text mention “前25” where applicable
- config and documentation regressions expect `STABLECOIN_DEPEG_TOP_N=25`
- existing exclusion behavior for `USYC` and `USDY` still holds under the expanded limit

## Success Criteria

This work is complete when:

- Telegram users see 25 stablecoins from `/stablecoins`
- the background depeg monitor evaluates 25 eligible stablecoins
- `USYC` and `USDY` remain excluded
- user-facing copy consistently says “前25” where relevant
- regression tests pass with the new top-25 scope
