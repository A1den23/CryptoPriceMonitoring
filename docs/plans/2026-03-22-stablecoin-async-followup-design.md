# Stablecoin Async Follow-up Design

**Date:** 2026-03-22
**Status:** Approved

## Goal

Eliminate the runtime risk introduced by the first stablecoin depeg monitor rollout, where stablecoin polling and alert delivery can block the main asyncio event loop and interfere with existing Binance WebSocket monitoring.

## Problem Summary

The current stablecoin monitor runs in a separate asyncio task, but still performs synchronous I/O:

- DefiLlama fetches use `requests`
- stablecoin alert delivery calls the synchronous Telegram notifier directly

Because those calls run inside the same event loop as the WebSocket monitor, slow HTTP or Telegram responses can delay BTC/ETH/SOL price processing.

## Chosen Approach

Use a **full async refactor for the stablecoin path**.

### Why this approach

- It removes the event-loop blocking risk at the source
- It keeps the runtime model consistent with the surrounding asyncio-based monitor orchestration
- It is a cleaner long-term design than wrapping synchronous calls in `asyncio.to_thread`

### Why not the smaller workaround

A smaller workaround would wrap the synchronous DefiLlama fetch and Telegram send in `asyncio.to_thread`. That would reduce blocking risk, but it would preserve a sync client API inside an async runtime and leave the stablecoin path architecturally inconsistent.

## Architecture

### Module boundaries

Keep the existing module split, but make the stablecoin path truly async:

- `common/clients/defillama.py`
  - async HTTP fetch implementation
  - existing parsing responsibility retained
- `monitor/stablecoin_depeg_monitor.py`
  - depeg state machine retained
  - polling flow becomes async
  - alert sending becomes non-blocking from the event loop’s perspective
- `monitor/ws_monitor.py`
  - continues to orchestrate the background stablecoin task
  - no major structural changes required

## Data Flow

1. `WebSocketMultiCoinMonitor.run()` starts the stablecoin monitor as a background asyncio task.
2. `StablecoinDepegMonitor.run()` repeatedly calls async `run_once()`.
3. `run_once()`:
   - awaits the DefiLlama async client fetch
   - evaluates each snapshot against the depeg state machine
   - asynchronously delivers alerts for snapshots that should notify
4. Failures in one stablecoin polling cycle are logged and do not terminate the main monitor.
5. Shutdown continues to use the existing task cancellation flow.

## Error Handling

### DefiLlama failures

- HTTP failures and malformed payload failures remain localized to the stablecoin monitor
- the stablecoin loop logs the failure and continues on the next poll interval

### Telegram failures

- failed alert delivery is logged
- failed delivery does not tear down the stablecoin task
- depeg state management remains simple and does not attempt retry queues in this follow-up

### Cancellation

- `asyncio.CancelledError` must continue to propagate cleanly
- the loop should not swallow cancellation while sleeping or awaiting the client

## Testing Strategy

Add or update regression coverage for:

- async DefiLlama client fetch success
- malformed payload handling in the async client path
- async stablecoin `run_once()` behavior
- polling loop failure-and-continue behavior
- cancellation/shutdown behavior
- existing websocket integration behavior after the async refactor

Final verification remains:

- `python3 -m unittest discover -s tests -p 'test_*.py'`
- `python3 -c "import common, monitor"`

## Documentation Updates

Update operator-facing docs so they match the shipped behavior:

- `DEPLOYMENT.md`
  - add the five stablecoin depeg environment variables to the recommended configuration section
- `README.md`
  - change the stablecoin threshold wording from hardcoded `1.05 / 0.95`
  - describe the behavior as default ±5%, controlled by `STABLECOIN_DEPEG_THRESHOLD_PERCENT`

## Non-Goals

This follow-up does **not**:

- refactor the entire notification system to async
- change the stablecoin alert threshold semantics
- change the websocket monitoring architecture
- add retries, backoff policies, or recovery notifications beyond current behavior

## Success Criteria

This follow-up is complete when:

- the stablecoin monitoring path no longer performs blocking DefiLlama I/O on the event loop
- stablecoin alert delivery no longer blocks the WebSocket monitor path
- docs are synchronized across README and deployment instructions
- the regression suite and import smoke test pass
