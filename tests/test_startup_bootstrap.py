import importlib
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock


WORKTREE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKTREE_ROOT))


class StartupBootstrapContractTests(unittest.TestCase):
    def _load_startup_module(self):
        return importlib.import_module("common.startup")

    def _build_config(self, cache_path: Path):
        return types.SimpleNamespace(stablecoin_universe_cache_path=str(cache_path))

    def test_refreshes_when_cache_missing(self) -> None:
        startup = self._load_startup_module()

        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "stablecoin_top25.json"
            refresh_cache = Mock()
            execute_command = Mock(return_value=0)

            result = startup.bootstrap_runtime(
                command=["python", "-m", "monitor"],
                config=self._build_config(cache_path),
                refresh_cache=refresh_cache,
                execute_command=execute_command,
            )

        refresh_cache.assert_called_once_with(cache_path)
        execute_command.assert_called_once_with(["python", "-m", "monitor"])
        self.assertEqual(result, 0)

    def test_skips_refresh_when_cache_file_exists(self) -> None:
        startup = self._load_startup_module()

        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "stablecoin_top25.json"
            cache_path.write_text("{}", encoding="utf-8")
            refresh_cache = Mock()
            execute_command = Mock(return_value=0)

            startup.bootstrap_runtime(
                command=["python", "-m", "monitor"],
                config=self._build_config(cache_path),
                refresh_cache=refresh_cache,
                execute_command=execute_command,
            )

        refresh_cache.assert_not_called()
        execute_command.assert_called_once_with(["python", "-m", "monitor"])

    def test_fails_fast_when_cache_path_is_an_existing_directory(self) -> None:
        startup = self._load_startup_module()

        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "stablecoin_top25.json"
            cache_path.mkdir()
            refresh_cache = Mock()
            execute_command = Mock(return_value=0)

            with self.assertRaisesRegex(
                ValueError,
                r"Stablecoin universe cache path must be a file path, got existing directory:",
            ):
                startup.bootstrap_runtime(
                    command=["python", "-m", "monitor"],
                    config=self._build_config(cache_path),
                    refresh_cache=refresh_cache,
                    execute_command=execute_command,
                )

        refresh_cache.assert_not_called()
        execute_command.assert_not_called()

    def test_executes_requested_command_after_refresh_check(self) -> None:
        startup = self._load_startup_module()
        events: list[tuple[str, object]] = []

        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "stablecoin_top25.json"

            def refresh_cache(path: Path) -> None:
                events.append(("refresh", path))

            def execute_command(command: list[str]) -> int:
                events.append(("command", tuple(command)))
                return 7

            result = startup.bootstrap_runtime(
                command=["python", "-m", "bot"],
                config=self._build_config(cache_path),
                refresh_cache=refresh_cache,
                execute_command=execute_command,
            )

        self.assertEqual(
            events,
            [
                ("refresh", cache_path),
                ("command", ("python", "-m", "bot")),
            ],
        )
        self.assertEqual(result, 7)

    def test_uses_configured_cache_path(self) -> None:
        startup = self._load_startup_module()

        with TemporaryDirectory() as temp_dir:
            configured_cache_path = Path(temp_dir) / "nested" / "stablecoin_cache.json"
            refresh_cache = Mock()
            execute_command = Mock(return_value=0)

            startup.bootstrap_runtime(
                command=["python", "-m", "monitor"],
                config=self._build_config(configured_cache_path),
                refresh_cache=refresh_cache,
                execute_command=execute_command,
            )

        refresh_cache.assert_called_once_with(configured_cache_path)


if __name__ == "__main__":
    unittest.main()
