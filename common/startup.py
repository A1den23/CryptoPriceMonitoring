import asyncio
import os
import sys
from pathlib import Path
from typing import Callable, Sequence


def bootstrap_runtime(
    command: Sequence[str],
    config: object,
    refresh_cache: Callable[[Path], None],
    execute_command: Callable[[Sequence[str]], int],
) -> int:
    cache_path = Path(config.stablecoin_universe_cache_path)

    if cache_path.is_dir():
        raise ValueError(
            "Stablecoin universe cache path must be a file path, got existing directory: "
            f"{cache_path}"
        )

    if not cache_path.is_file():
        refresh_cache(cache_path)

    return execute_command(command)


async def _refresh_cache(cache_path: Path, config: object) -> None:
    from common.stablecoin_universe import refresh_stablecoin_universe
    from common.clients.defillama import DefiLlamaClient

    async with DefiLlamaClient() as client:
        await refresh_stablecoin_universe(
            client,
            cache_path,
            top_n=config.stablecoin_depeg_top_n,
        )


def _execute_command(command: Sequence[str]) -> int:
    os.execvp(command[0], list(command))
    return 0


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python -m common.startup <command> [args...]")

    from common import ConfigManager, load_environment

    load_environment()
    config = ConfigManager()
    command = sys.argv[1:]
    exit_code = bootstrap_runtime(
        command=command,
        config=config,
        refresh_cache=lambda cache_path: asyncio.run(_refresh_cache(cache_path, config)),
        execute_command=_execute_command,
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
