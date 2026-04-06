from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from common import ConfigManager, load_environment
from common.clients.defillama import DefiLlamaClient, StablecoinSnapshot
from common.logging import logger
from common.utils import now_in_configured_timezone


@dataclass(frozen=True, slots=True)
class CachedStablecoinUniverse:
    refreshed_at: str
    top_n: int
    snapshots: list[StablecoinSnapshot]


def _normalize_cache_path(cache_path: str | Path) -> Path:
    return Path(cache_path)


def _serialize_snapshot(snapshot: StablecoinSnapshot) -> dict[str, str | float | int]:
    return {
        "name": snapshot.name,
        "symbol": snapshot.symbol,
        "price": snapshot.price,
        "circulating": snapshot.circulating,
        "rank": snapshot.rank,
    }


def write_cached_stablecoin_universe(universe: CachedStablecoinUniverse, cache_path: str | Path) -> None:
    path = _normalize_cache_path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "refreshed_at": universe.refreshed_at,
        "top_n": universe.top_n,
        "snapshots": [_serialize_snapshot(snapshot) for snapshot in universe.snapshots],
    }

    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp_file:
        json.dump(payload, temp_file, ensure_ascii=False)
        temp_name = temp_file.name

    Path(temp_name).replace(path)


def load_cached_stablecoin_universe(cache_path: str | Path) -> CachedStablecoinUniverse:
    path = _normalize_cache_path(cache_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        snapshots = [
            StablecoinSnapshot(
                name=str(item["name"]),
                symbol=str(item["symbol"]),
                price=float(item["price"]),
                circulating=float(item["circulating"]),
                rank=int(item["rank"]),
            )
            for item in payload["snapshots"]
        ]
        return CachedStablecoinUniverse(
            refreshed_at=str(payload["refreshed_at"]),
            top_n=int(payload["top_n"]),
            snapshots=snapshots,
        )
    except FileNotFoundError as exc:
        raise ValueError(f"Stablecoin universe cache not found: {path}") from exc
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid stablecoin universe cache: {path}") from exc


async def refresh_stablecoin_universe(
    client,
    cache_path: str | Path,
    top_n: int = 25,
) -> CachedStablecoinUniverse:
    snapshots = await client.fetch_stablecoins(top_n=top_n)
    universe = CachedStablecoinUniverse(
        refreshed_at=now_in_configured_timezone().isoformat(),
        top_n=top_n,
        snapshots=snapshots,
    )
    write_cached_stablecoin_universe(universe, cache_path)
    return universe


async def resolve_live_snapshots_for_cached_universe(
    client,
    cache_path: str | Path,
) -> list[StablecoinSnapshot]:
    cached = load_cached_stablecoin_universe(cache_path)
    live_snapshots = await client.fetch_all_stablecoins()
    live_by_symbol_and_name = {
        (snapshot.symbol, snapshot.name): snapshot for snapshot in live_snapshots
    }
    live_by_symbol = {snapshot.symbol: snapshot for snapshot in live_snapshots}

    resolved: list[StablecoinSnapshot] = []
    for cached_snapshot in cached.snapshots:
        live_snapshot = live_by_symbol_and_name.get(
            (cached_snapshot.symbol, cached_snapshot.name)
        )
        if live_snapshot is None:
            live_snapshot = live_by_symbol.get(cached_snapshot.symbol)
        if live_snapshot is None:
            raise ValueError(
                f"Cached stablecoin missing from live data: {cached_snapshot.symbol}"
            )
        resolved.append(
            StablecoinSnapshot(
                name=live_snapshot.name,
                symbol=live_snapshot.symbol,
                price=live_snapshot.price,
                circulating=live_snapshot.circulating,
                rank=cached_snapshot.rank,
            )
        )
    return resolved


async def _refresh_from_config(config: ConfigManager) -> CachedStablecoinUniverse:
    async with DefiLlamaClient() as client:
        return await refresh_stablecoin_universe(
            client,
            config.stablecoin_universe_cache_path,
            top_n=config.stablecoin_depeg_top_n,
        )


def main() -> None:
    load_environment()
    if len(sys.argv) != 2 or sys.argv[1] != "refresh":
        raise SystemExit("Usage: python -m common.stablecoin_universe refresh")

    config = ConfigManager()
    universe = asyncio.run(_refresh_from_config(config))
    logger.info(
        f"Stablecoin universe refreshed: cache={config.stablecoin_universe_cache_path}, snapshots={len(universe.snapshots)}"
    )


if __name__ == "__main__":
    main()
