# Bot /price Coin Selector Design

## Goal

Improve the Telegram bot's single-coin query experience so users can type `/price` without arguments and immediately choose from currently monitored coins, while preserving direct `/price BTC` lookups.

## Current Context

The bot already supports:

- command-based queries via `bot/handlers.py`
- inline keyboard interactions via `price_<coin>` callback data
- reusable message rendering helpers in `bot/messages.py`
- single-coin fetching through `send_price_update()`

Today, `/price` without arguments returns an input error. That is functional, but it forces users to remember coin codes even though the bot already knows the enabled coin list and already renders coin-selection buttons elsewhere.

## User Experience Design

### Mode 1: `/price` with no arguments

Behavior:

- bot replies with a short prompt such as "请选择要查看的币种"
- message includes inline buttons for all enabled coins
- message may also include a "查看全部价格" button

Result:

- users can discover supported coins without memorizing symbols
- the flow matches the bot's existing button-based interaction model

### Mode 2: `/price BTC`

Behavior:

- keeps current command-driven lookup flow
- validates input as today
- if valid and enabled, returns the BTC detail view directly
- if invalid, keeps current suggestion/error behavior

Result:

- power users keep the fast path
- no regression for users already relying on `/price BTC`

### Mode 3: Button click after `/price`

Behavior:

- clicking a coin button routes through existing callback handling
- bot renders that coin's detail page
- detail page keeps refresh and coin-switching controls

Result:

- one consistent interaction model for both typed and button-driven access

## Detail View Design

The single-coin detail view should become slightly richer than the current minimal price response.

Recommended fields:

- coin display name
- trading pair symbol
- current price
- milestone threshold
- volatility threshold and window
- enabled status
- updated timestamp

Buttons:

- refresh current coin
- view all prices
- switch to another enabled coin

This remains a lightweight query view. It does not attempt to show alert history, cached state, or monitoring internals.

## Technical Design

### Handler changes

File: `bot/handlers.py`

- update `price_command()` so that:
  - no args => render coin picker instead of validation error
  - args present => keep current validation path
- keep `send_price_update()` as the single place that resolves a coin and fetches its latest price
- avoid changing callback data format to preserve compatibility with current button handling

### Message rendering changes

File: `bot/messages.py`

- add a small renderer for the `/price` no-argument picker prompt
- expand or replace the single-coin message renderer so it can output the richer coin detail view
- reuse existing keyboard builders where possible instead of introducing new button systems

### App surface changes

File: `bot/app.py`

- reuse existing helper methods already exposed on `TelegramBot`
- only add wrapper methods if a message helper must be reachable from handlers in a clean way
- do not change the bot startup lifecycle or polling behavior

## Scope Boundaries

### In scope

- `/price` no-argument coin picker
- richer single-coin detail response
- reusing existing inline keyboard flow
- help text updates if command behavior needs clarification
- targeted tests for new behavior

### Out of scope

- new `/coin` command
- alert history
- persistent user preferences
- caching or stateful sessions
- monitor runtime changes
- changes to deployment or entrypoint behavior

## Risks and Mitigations

### Risk: `/price` behavior drift

Mitigation:

- only change the no-argument branch
- preserve current validation and suggestion behavior for argument-based usage

### Risk: callback flow regression

Mitigation:

- keep existing `price_<coin>` callback contract
- test both command and button paths

### Risk: command/help mismatch

Mitigation:

- update help text if needed so `/price` clearly supports both direct input and button-driven selection

## Test Strategy

Primary coverage should include:

- `/price` with no args returns a coin picker instead of an error
- `/price BTC` still returns coin detail
- unknown coin still returns suggestion/error behavior
- disabled coin still returns the correct rejection path
- price buttons still open the same coin detail view
- detail view rendering includes the new fields and expected buttons

## Recommendation

Implement this as a focused bot-only enhancement. It has clear user value, fits the current architecture, and avoids unnecessary expansion into monitoring-state or persistence features.