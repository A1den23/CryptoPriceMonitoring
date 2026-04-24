#!/usr/bin/env python3
"""Docker health check for Crypto Price Monitoring services.

Verifies that the active service heartbeat file is fresh (modified within
the last 180 seconds).  The heartbeat file is selected based on the main
process command line:
  - ``-m monitor`` -> ``MONITOR_HEARTBEAT_FILE`` or ``/tmp/monitor_heartbeat``
  - ``-m bot``     -> ``BOT_HEARTBEAT_FILE`` or ``/tmp/bot_heartbeat``
"""

import os
import pathlib
import sys
import time

HEARTBEAT_STALE_SECONDS = 180

HEARTBEAT_MAP = {
    b"-m monitor": ("MONITOR_HEARTBEAT_FILE", "/tmp/monitor_heartbeat"),
    b"-m bot": ("BOT_HEARTBEAT_FILE", "/tmp/bot_heartbeat"),
}


def _resolve_heartbeat_path(cmdline: bytes) -> pathlib.Path | None:
    for marker, (env_name, default_path) in HEARTBEAT_MAP.items():
        if marker not in cmdline:
            continue
        return pathlib.Path(os.getenv(env_name, default_path))
    return None


def main() -> None:
    cmdline = pathlib.Path("/proc/1/cmdline").read_bytes().replace(b"\x00", b" ")
    now = time.time()

    heartbeat = _resolve_heartbeat_path(cmdline)
    if heartbeat is None:
        # No matching marker found - unknown service.
        sys.exit(1)
    if not heartbeat.exists():
        sys.exit(1)
    if now - heartbeat.stat().st_mtime >= HEARTBEAT_STALE_SECONDS:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
