import json
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from tests.stubs import install_dependency_stubs


WORKTREE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKTREE_ROOT))


install_dependency_stubs()

from common.clients.defillama import StablecoinSnapshot
from common.stablecoin_universe import (
    load_cached_stablecoin_universe,
    refresh_stablecoin_universe,
    refresh_stablecoin_universe_from_config,
    resolve_live_snapshots_for_cached_universe,
    write_cached_stablecoin_universe,
    CachedStablecoinUniverse,
    compute_next_stablecoin_universe_refresh_time,
)


class StablecoinUniverseCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_writes_cache_file(self) -> None:
        snapshots = [StablecoinSnapshot("Tether", "USDT", 1.0, 100.0, 1)]
        client = types.SimpleNamespace(fetch_stablecoins=AsyncMock(return_value=snapshots))

        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "stablecoin_top25.json"

            cached = await refresh_stablecoin_universe(client, cache_path)
            reloaded = load_cached_stablecoin_universe(cache_path)

        self.assertEqual(client.fetch_stablecoins.await_count, 1)
        self.assertEqual(client.fetch_stablecoins.await_args.args, ())
        self.assertEqual(client.fetch_stablecoins.await_args.kwargs, {"top_n": 25})
        self.assertEqual(cached.top_n, 25)
        self.assertEqual([item.symbol for item in cached.snapshots], ["USDT"])
        self.assertEqual(reloaded.top_n, 25)
        self.assertEqual([item.symbol for item in reloaded.snapshots], ["USDT"])

    async def test_refresh_failure_keeps_previous_cache_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "stablecoin_top25.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "refreshed_at": "2026-04-05T02:00:00+08:00",
                        "top_n": 25,
                        "snapshots": [
                            {
                                "name": "Tether",
                                "symbol": "USDT",
                                "price": 1.0,
                                "circulating": 100.0,
                                "rank": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            original = cache_path.read_text(encoding="utf-8")
            client = types.SimpleNamespace(fetch_stablecoins=AsyncMock(side_effect=RuntimeError("boom")))

            with self.assertRaises(RuntimeError):
                await refresh_stablecoin_universe(client, cache_path)

            self.assertEqual(cache_path.read_text(encoding="utf-8"), original)

    def test_compute_next_stablecoin_universe_refresh_time_uses_same_day_when_time_is_in_future(self) -> None:
        now = datetime(2026, 4, 6, 1, 30, tzinfo=timezone.utc)

        next_refresh = compute_next_stablecoin_universe_refresh_time(
            now,
            refresh_hour=2,
            refresh_minute=0,
        )

        self.assertEqual(next_refresh, datetime(2026, 4, 6, 2, 0, tzinfo=timezone.utc))

    def test_compute_next_stablecoin_universe_refresh_time_rolls_to_next_day_when_time_has_passed(self) -> None:
        now = datetime(2026, 4, 6, 2, 1, tzinfo=timezone.utc)

        next_refresh = compute_next_stablecoin_universe_refresh_time(
            now,
            refresh_hour=2,
            refresh_minute=0,
        )

        self.assertEqual(next_refresh, datetime(2026, 4, 7, 2, 0, tzinfo=timezone.utc))

    async def test_refresh_stablecoin_universe_from_config_uses_cache_path_and_top_n(self) -> None:
        snapshots = [StablecoinSnapshot("Tether", "USDT", 1.0, 100.0, 1)]
        client = types.SimpleNamespace(fetch_stablecoins=AsyncMock(return_value=snapshots))

        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "stablecoin_top25.json"
            config = types.SimpleNamespace(
                stablecoin_universe_cache_path=str(cache_path),
                stablecoin_depeg_top_n=12,
            )

            cached = await refresh_stablecoin_universe_from_config(config, client)

        self.assertEqual(cached.top_n, 12)
        client.fetch_stablecoins.assert_awaited_once_with(top_n=12)

    async def test_load_cached_stablecoin_universe_raises_for_missing_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "missing.json"

            with self.assertRaises(ValueError):
                load_cached_stablecoin_universe(cache_path)

    async def test_load_cached_stablecoin_universe_raises_for_invalid_json(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "stablecoin_top25.json"
            cache_path.write_text("{not-json", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_cached_stablecoin_universe(cache_path)

    async def test_load_cached_stablecoin_universe_raises_for_invalid_payload_shape(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "stablecoin_top25.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "refreshed_at": "2026-04-05T02:00:00+08:00",
                        "top_n": 25,
                        "snapshots": [{"symbol": "USDT"}],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_cached_stablecoin_universe(cache_path)

    async def test_resolve_live_snapshots_for_cached_universe_preserves_cached_sequence_and_rank_values(self) -> None:
        cached = CachedStablecoinUniverse(
            refreshed_at="2026-04-05T02:00:00+08:00",
            top_n=2,
            snapshots=[
                StablecoinSnapshot("USD Coin", "USDC", 1.0, 100.0, 2),
                StablecoinSnapshot("Tether USD", "USDT", 1.0, 200.0, 1),
            ],
        )
        live_snapshots = [
            StablecoinSnapshot("Tether", "USDT", 0.999, 210.0, 1),
            StablecoinSnapshot("USDC", "USDC", 1.001, 110.0, 2),
            StablecoinSnapshot("DAI", "DAI", 1.0, 50.0, 3),
        ]
        client = types.SimpleNamespace(fetch_all_stablecoins=AsyncMock(return_value=live_snapshots))

        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "stablecoin_top25.json"
            write_cached_stablecoin_universe(cached, cache_path)

            resolved = await resolve_live_snapshots_for_cached_universe(client, cache_path)

        self.assertEqual([snapshot.symbol for snapshot in resolved], ["USDC", "USDT"])
        self.assertEqual([snapshot.rank for snapshot in resolved], [2, 1])
        self.assertEqual([snapshot.name for snapshot in resolved], ["USDC", "Tether"])
        self.assertEqual([snapshot.price for snapshot in resolved], [1.001, 0.999])
        self.assertEqual([snapshot.circulating for snapshot in resolved], [110.0, 210.0])
        client.fetch_all_stablecoins.assert_awaited_once_with()

    async def test_resolve_live_snapshots_for_cached_universe_matches_by_name_when_symbols_are_duplicated(self) -> None:
        cached = CachedStablecoinUniverse(
            refreshed_at="2026-04-05T02:00:00+08:00",
            top_n=1,
            snapshots=[
                StablecoinSnapshot("Solstice USX", "USX", 1.0, 356_000_000.0, 20),
            ],
        )
        live_snapshots = [
            StablecoinSnapshot("Solstice USX", "USX", 0.999, 356_656_267.89, 22),
            StablecoinSnapshot("dForce USD", "USX", 0.478, 7_234_313.94, 87),
        ]
        client = types.SimpleNamespace(fetch_all_stablecoins=AsyncMock(return_value=live_snapshots))

        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "stablecoin_top25.json"
            write_cached_stablecoin_universe(cached, cache_path)

            resolved = await resolve_live_snapshots_for_cached_universe(client, cache_path)

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].name, "Solstice USX")
        self.assertEqual(resolved[0].symbol, "USX")
        self.assertEqual(resolved[0].price, 0.999)
        self.assertEqual(resolved[0].circulating, 356_656_267.89)
        self.assertEqual(resolved[0].rank, 20)

    async def test_resolve_live_snapshots_for_cached_universe_raises_when_cached_symbol_missing_from_live_data(self) -> None:
        cached = CachedStablecoinUniverse(
            refreshed_at="2026-04-05T02:00:00+08:00",
            top_n=2,
            snapshots=[
                StablecoinSnapshot("USD Coin", "USDC", 1.0, 100.0, 2),
                StablecoinSnapshot("Tether USD", "USDT", 1.0, 200.0, 1),
            ],
        )
        client = types.SimpleNamespace(
            fetch_all_stablecoins=AsyncMock(
                return_value=[StablecoinSnapshot("USDC", "USDC", 1.001, 110.0, 2)]
            )
        )

        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "stablecoin_top25.json"
            write_cached_stablecoin_universe(cached, cache_path)

            # Missing symbols are now skipped instead of raising.
            result = await resolve_live_snapshots_for_cached_universe(client, cache_path)
            # Only the USDC snapshot should be resolved (USDT is missing from live data).
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].symbol, "USDC")


if __name__ == "__main__":
    unittest.main()
