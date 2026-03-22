# Stablecoin depeg monitor design

## Summary

Add a second monitoring path to the existing monitor process that watches the top 20 stablecoins from DefiLlama and sends a Telegram alert when any stablecoin deviates from the $1 peg by more than 5%.

This feature should run alongside the existing Binance WebSocket monitor without changing the current BTC/ETH/SOL alert model.

## Goals

- Monitor the top 20 stablecoins from DefiLlama dynamically
- Alert when price is greater than 1.05 or less than 0.95
- Reuse the existing Telegram notification path
- Avoid alert spam with per-stablecoin cooldowns
- Keep the implementation isolated from the existing Binance price monitor logic

## Non-goals

- Replacing the current Binance-based monitor flow
- Adding stablecoin support to `COIN_LIST`
- Sharing stablecoin state with the Telegram bot in the first iteration
- Sending recovery notifications in the first iteration
- Persisting state across process restarts

## Current project fit

The current codebase has two major runtime paths:

- `monitor/` for long-running market monitoring and Telegram alerts
- `bot/` for on-demand Telegram interactions

The existing monitor path is centered around Binance WebSocket streams and `PriceMonitor`, which evaluates milestone, volatility, and volume alerts for a fixed configured coin set. That model is not a good fit for dynamic top-N stablecoin monitoring driven by a third-party ranking API.

The clean extension point is the monitor process itself, specifically the runtime orchestration in `monitor/ws_monitor.py`, which already owns lifecycle management, heartbeat updates, and Telegram notification integration.

## Proposed architecture

Add a new monitor component that runs in parallel with the existing WebSocket monitor:

- Existing: `WebSocketMultiCoinMonitor` continues to manage Binance WebSocket monitoring for configured coins
- New: `StablecoinDepegMonitor` runs as a background async loop in the same process

The new component will:

1. Poll DefiLlama on a fixed interval
2. Select the top N stablecoins by rank from the API response
3. Evaluate each stablecoin against the depeg threshold relative to $1
4. Track per-stablecoin alert state in memory
5. Send Telegram alerts through the existing `TelegramNotifier`

This design keeps stablecoin-specific logic separate from the current `PriceMonitor` code and avoids bending the Binance-oriented config model to fit a different monitoring domain.

## Proposed files

- `common/clients/defillama.py`
  - DefiLlama API client and response parsing
- `monitor/stablecoin_depeg_monitor.py`
  - Polling loop, state tracking, threshold checks, alerting
- `common/config.py`
  - Stablecoin depeg config parsing
- `monitor/ws_monitor.py`
  - Start and stop the stablecoin monitor task as part of monitor runtime
- `tests/test_regressions.py`
  - Regression tests for parsing, threshold logic, cooldowns, and re-alert behavior

## Configuration

Add these environment variables:

```env
STABLECOIN_DEPEG_MONITOR_ENABLED=true
STABLECOIN_DEPEG_TOP_N=20
STABLECOIN_DEPEG_THRESHOLD_PERCENT=5
STABLECOIN_DEPEG_POLL_INTERVAL_SECONDS=300
STABLECOIN_DEPEG_ALERT_COOLDOWN_SECONDS=3600
```

### Semantics

- `STABLECOIN_DEPEG_MONITOR_ENABLED`
  - Enables or disables the feature
- `STABLECOIN_DEPEG_TOP_N`
  - Number of top-ranked stablecoins to monitor from DefiLlama
- `STABLECOIN_DEPEG_THRESHOLD_PERCENT`
  - Percent deviation from $1 required to trigger an alert
- `STABLECOIN_DEPEG_POLL_INTERVAL_SECONDS`
  - Poll cadence for DefiLlama API
- `STABLECOIN_DEPEG_ALERT_COOLDOWN_SECONDS`
  - Minimum time between repeated alerts for the same stablecoin while it remains depegged

### Alert threshold

With the approved 5% threshold:

- alert if `price > 1.05`
- alert if `price < 0.95`

## Data flow

1. Monitor process starts normally
2. `ConfigManager` loads stablecoin depeg settings
3. `WebSocketMultiCoinMonitor.run()` starts the Binance WebSocket task as it does today
4. If stablecoin depeg monitoring is enabled, it also starts a `StablecoinDepegMonitor.run()` task
5. The stablecoin task polls DefiLlama every configured interval
6. It extracts the top N stablecoins and their current prices
7. For each stablecoin, it computes peg deviation relative to $1
8. It applies state-aware alert logic
9. It sends Telegram notifications through the shared notifier
10. On monitor shutdown, the stablecoin task is cancelled cleanly along with the WebSocket runtime

## Alert state model

Track state per stablecoin in memory using a small state record such as:

- `is_depegged`
- `last_alert_time`
- `last_seen_price`
- `last_rank`

### State transitions

#### Entering depeg state
When a stablecoin moves from normal range into a depegged range:

- mark as depegged
- send an alert immediately
- store alert time

#### Remaining depegged
When a stablecoin is still depegged on later polls:

- only send another alert if cooldown has elapsed
- update price/rank state on each poll

#### Returning to normal
When a stablecoin returns to the normal range `[0.95, 1.05]`:

- clear the depegged state
- do not send a recovery message in v1
- allow a future fresh depeg event to alert immediately again

## Notification format

Example:

```text
🚨🚨【稳定币脱锚警报】🚨🚨
🪙 USDC
🏅 排名: #3
💰 当前价格: $0.9430
📉 偏离锚定: -5.70%
🎯 阈值: 5.00%
⏱️ 2026-03-22 14:30:00
```

If the price is above peg, show the deviation with an up indicator instead.

The message should include:

- stablecoin display name or symbol
- current rank
- current price
- signed deviation percent from $1
- configured threshold
- timestamp

## Error handling

- DefiLlama fetch failures should be logged and skipped for that cycle
- A failed poll should not stop the monitor process
- Malformed API entries should be ignored with logging
- If fewer than top N valid stablecoins are returned, monitor the valid subset for that cycle
- Notification failures should follow the same notifier error behavior as the current monitor

## Security and operational notes

- Do not log secrets or Telegram credentials
- Reuse the existing logging and notification utilities
- Keep DefiLlama integration read-only
- Keep polling interval conservative to avoid unnecessary request volume
- Keep all state in memory for v1

## Testing strategy

Add regression tests for:

1. DefiLlama parsing
   - valid entries are parsed
   - malformed entries are skipped safely
   - top-N selection works correctly

2. Threshold logic
   - 1.049 does not alert
   - 1.051 alerts
   - 0.951 does not alert
   - 0.949 alerts

3. Cooldown behavior
   - first depeg alerts immediately
   - repeated polls during cooldown do not re-alert
   - repeated polls after cooldown re-alert

4. Reset behavior
   - returning to normal clears depeg state
   - a new later depeg triggers a fresh alert

5. Runtime integration
   - stablecoin monitor task can run alongside existing monitor orchestration
   - shutdown cancels the task cleanly

## Acceptance criteria

- The monitor process can optionally run stablecoin depeg monitoring using DefiLlama data
- Top 20 stablecoins are evaluated on each poll
- A Telegram alert is sent when a stablecoin moves outside the ±5% band around $1
- Repeated alerts for the same stablecoin are throttled by cooldown
- Returning to normal resets alert eligibility without sending a recovery alert
- Existing Binance monitor behavior remains unchanged

## Open questions resolved

- Data source: DefiLlama API
- Scope: top 20 stablecoins
- Depeg rule: deviation from $1 greater than 5%
- Initial behavior on recovery: no recovery notification in v1

## Recommendation

Implement this as a parallel monitor component under `monitor/` with its own API client and in-memory state machine. This keeps the code aligned with current architecture and avoids forcing a dynamic ranking-based monitor into the existing fixed-coin Binance alert model.
