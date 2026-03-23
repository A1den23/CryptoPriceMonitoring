# Telegram Stablecoins Command Design

**Date:** 2026-03-22
**Status:** Approved

## Goal

Add a one-command Telegram interaction so operators can view the latest top 20 stablecoin prices on demand.

## Chosen Entry Point

Use a new `/stablecoins` bot command.

## Why This Approach

- It matches the requested interaction model directly.
- It keeps `/all` focused on the currently monitored trading pairs.
- It avoids expanding the `/start` keyboard for a feature that was explicitly requested as a command.
- It requires only small, localized bot changes.

## Scope

This change adds:

- a new `/stablecoins` Telegram command
- bot-side fetching of the top 20 stablecoins through `DefiLlamaClient`
- a formatted Telegram response showing rank, symbol, name, price, and deviation from `$1`
- help and welcome text updates mentioning the new command

This change does not add:

- pagination
- inline refresh buttons
- filtering or search
- new background polling
- coupling to the stablecoin depeg monitor enable/disable flag

## Architecture

### Command registration

Register a new `CommandHandler("stablecoins", ...)` in `bot/app.py` alongside the existing bot commands.

### Data fetch path

The command handler in `bot/handlers.py` will create or use a `DefiLlamaClient` and call its async `fetch_stablecoins(top_n=20)` API.

This keeps the Telegram query path read-only and separate from the long-running stablecoin monitor.

### Message rendering

Add a dedicated rendering helper in `bot/messages.py` that formats:

- title for the stablecoin list
- each item as rank + symbol + name
- current price
- deviation from `$1`
- timestamp footer

## Behavior

- `/stablecoins` should work even if `STABLECOIN_DEPEG_MONITOR_ENABLED=false`
- the command always queries the latest snapshot on demand
- if DefiLlama returns malformed data or the request fails, the bot sends a short failure message instead of crashing
- the command returns top 20 stablecoins sorted by circulating supply, consistent with the existing client behavior

## Error Handling

- HTTP or parsing failures from `DefiLlamaClient` are caught in the bot command handler
- the user sees a concise Telegram error message
- the exception is logged for debugging

## Testing Strategy

Add regression coverage for:

- `/stablecoins` success path returns a formatted top-20 stablecoin list
- `/stablecoins` failure path returns an error message
- `/help` and `/start` text mention the new command

Use fake async client behavior in tests rather than live network calls.

## Success Criteria

This work is complete when:

- Telegram users can run `/stablecoins`
- the bot returns the latest top 20 stablecoin snapshot in a readable format
- failures are handled gracefully
- bot help text documents the command
- regression tests cover the new behavior
