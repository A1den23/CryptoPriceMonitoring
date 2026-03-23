# Stablecoin Exclusion Design

**Date:** 2026-03-23
**Status:** Approved

## Goal

Exclude `USYC` (Circle USYC) and `USDY` (Ondo US Dollar Yield) from both the Telegram `/stablecoins` list and the background stablecoin depeg monitor.

## Chosen Approach

Apply a shared exclusion rule in the DefiLlama client layer so both stablecoin consumers use the same filtered universe.

## Why This Approach

- It keeps `/stablecoins` and background depeg monitoring consistent.
- It avoids duplicating the same exclusion logic in multiple call sites.
- It preserves the existing `top_n` contract by filtering first and then ranking/selecting.
- It keeps the change small and local to the stablecoin data pipeline.

## Scope

This change adds:

- a fixed symbol exclusion for `USYC` and `USDY`
- shared filtering in the DefiLlama client before sorting and rank assignment
- regression coverage proving excluded symbols are omitted and top-N results still backfill correctly

This change does not add:

- a config-driven exclusion list
- heuristic detection of yield-bearing stablecoins
- Telegram UX changes
- new runtime settings

## Architecture

### Shared filtering layer

`common/clients/defillama.py` remains the single source of truth for stablecoin snapshot selection.

After parsing valid entries from the DefiLlama payload, the client will exclude assets whose symbol is `USYC` or `USDY`.

### Ranking behavior

Filtering happens before sorting and top-N truncation.

That means:

- excluded assets never appear in `/stablecoins`
- excluded assets are never evaluated by the background depeg monitor
- `top_n=20` means the top 20 remaining eligible stablecoins, not “raw top 20 minus exclusions”

### Downstream impact

No handler or monitor-specific filtering is needed.

Existing downstream consumers continue to call `fetch_stablecoins(top_n=...)` and receive a pre-filtered ranked list.

## Behavior

- `USYC` and `USDY` are omitted from all DefiLlama-derived stablecoin results
- `/stablecoins` returns the next eligible assets so the list still fills to 20 when enough eligible entries exist
- background depeg monitoring ignores `USYC` and `USDY`
- rank numbering reflects the filtered result set

## Error Handling

- existing payload validation and parsing behavior stays unchanged
- excluded symbols are silently skipped, just like other filtered-out non-eligible entries
- no new user-facing error states are introduced

## Testing Strategy

Add regression coverage for:

- `DefiLlamaClient.parse_stablecoins(...)` excludes `USYC` and `USDY`
- when excluded symbols would otherwise appear in the top results, the client backfills with the next eligible stablecoins and returns a full top-N subset
- existing `/stablecoins` and stablecoin monitor tests continue to pass against the shared filtered client behavior

## Success Criteria

This work is complete when:

- `/stablecoins` no longer shows `USYC` or `USDY`
- the background stablecoin depeg monitor no longer evaluates `USYC` or `USDY`
- filtered results still return the requested top-N eligible stablecoins
- regression tests cover the exclusion behavior
