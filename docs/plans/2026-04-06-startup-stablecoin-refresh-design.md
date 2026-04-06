# Startup Stablecoin Refresh Design

**Date:** 2026-04-06
**Status:** Approved

## Goal

Make container startup automatically generate the stablecoin universe cache exactly once when the cache file does not exist.

This should prevent `/stablecoins` and stablecoin depeg monitoring from failing on first startup in a fresh environment.

## Chosen Approach

Use a shared startup bootstrap step for both Docker services.

Before launching `python -m monitor` or `python -m bot`, the container checks whether `STABLECOIN_UNIVERSE_CACHE_PATH` exists. If the file is missing, it runs `python -m common.stablecoin_universe refresh`. If the file already exists, it skips refresh and starts the main process immediately.

## Why This Approach

- It matches the desired behavior exactly: refresh only when the cache is missing.
- It keeps startup dependency handling in the startup path instead of scattering it across runtime request handlers.
- It preserves the existing single-shared-universe model used by both bot and monitor.
- It avoids repeated refreshes on every restart.
- It makes failures visible early, before the service appears healthy.

## Alternatives Considered

### 1. Startup bootstrap script (chosen)

Use a shared entrypoint or wrapper command for both services that checks for the cache file and refreshes only if absent.

**Pros**
- Centralized behavior
- Predictable startup contract
- Shared by both services
- Avoids runtime duplication and race-prone lazy logic

**Cons**
- First startup depends on DefiLlama availability

### 2. Lazy refresh inside bot and monitor

Each runtime refreshes on first use when the cache is missing.

**Pros**
- Startup can proceed without the cache

**Cons**
- Duplicates logic across services
- Introduces runtime branching and concurrency concerns
- Makes failures happen later and less predictably

### 3. Separate init service in Compose

Add a dedicated init-like service that generates the cache before the main services start.

**Pros**
- Very explicit separation of responsibilities

**Cons**
- Heavier Compose orchestration than needed for this project
- More moving parts for a simple startup dependency

## Scope

This change adds:

- a shared startup-time cache existence check
- conditional one-shot refresh when the stablecoin universe cache is missing
- startup failure if the initial refresh cannot be completed
- test coverage for the startup bootstrap behavior
- updated deployment and README instructions describing the new bootstrap behavior

This change does not add:

- refresh-on-every-start behavior
- application-level lazy fallback for missing cache
- replacement of the existing daily cron refresh workflow
- background retries hidden inside bot or monitor request paths

## Architecture

### Shared bootstrap behavior

Both `crypto-monitor` and `crypto-bot` should use the same startup wrapper.

The wrapper must:

1. read `STABLECOIN_UNIVERSE_CACHE_PATH`
2. check whether the file exists
3. run `python -m common.stablecoin_universe refresh` only when the file is missing
4. start the requested main process with `exec`

This keeps both services aligned on the same cache contract.

### Cache path handling

The bootstrap step should respect the existing configured cache path from `STABLECOIN_UNIVERSE_CACHE_PATH`.

That means the behavior works for both the default local path and the Docker-recommended `/app/data/stablecoin_top25.json` path without special cases.

### Failure semantics

If the cache is missing and the refresh command fails, the service should fail startup.

This is preferable to starting successfully and only failing later when `/stablecoins` is called or when the depeg monitor first reads the cache.

### Existing daily refresh remains

The current daily refresh workflow stays in place.

Startup bootstrap only guarantees first-run initialization. It does not replace the scheduled daily refresh that updates the universe later.

## Behavior

- fresh environment with no cache: startup performs one refresh, then launches service
- restart with existing cache: startup skips refresh and launches service immediately
- shared volume with existing cache: both services reuse the same file
- failed initial refresh: container startup fails loudly
- later daily refreshes continue using the existing manual/cron workflow

## Error Handling

- missing cache triggers refresh exactly once per startup attempt
- refresh errors are written to container logs
- failed refresh prevents the main service from starting
- existing cache is never deleted or replaced unless the refresh command itself succeeds
- services do not silently fall back to independent live top-N selection

## Testing Strategy

Add or update tests to prove:

- startup performs refresh when the configured cache file is missing
- startup skips refresh when the configured cache file already exists
- the startup wrapper preserves the target command (`monitor` vs `bot`)
- Compose and deployment docs describe the automatic first-run bootstrap and the continued daily refresh workflow

## Success Criteria

This work is complete when:

- a fresh `docker compose up` no longer requires a manual pre-run refresh
- `/stablecoins` does not fail on first startup solely because the cache is absent
- stablecoin depeg monitoring can start in a fresh environment without manual cache bootstrapping
- restarts do not force an unnecessary refresh when the cache already exists
- the daily refresh workflow remains available for ongoing universe updates
