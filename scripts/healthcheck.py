#!/usr/bin/env python3
"""Docker health check for Crypto Price Monitoring services.

Verifies that the active service heartbeat file is fresh (modified within
the last 180 seconds).  The heartbeat file is selected based on the main
process command line:
  - ``-m monitor`` -> ``/tmp/monitor_heartbeat``
  - ``-m bot``     -> ``/tmp/bot_heartbeat``
"""

import pathlib
import sys
import time

HEARTBEAT_STALE_SECONDS = 180

HEARTBEAT_MAP = {
    b"-m monitor": "/tmp/monitor_heartbeat",
    b"-m bot": "/tmp/bot_heartbeat",
}


def main() -> None:
    cmdline = pathlib.Path("/proc/1/cmdline").read_bytes().replace(b"\x00", b" ")
    now = time.time()

    for marker, path in HEARTBEAT_MAP.items():
        if marker not in cmdline:
            continue
        heartbeat = pathlib.Path(path)
        if not heartbeat.exists():
            sys.exit(1)
        if now - heartbeat.stat().st_mtime >= HEARTBEAT_STALE_SECONDS:
            sys.exit(1)
        sys.exit(0)

    # No matching marker found – unknown service.
    sys.exit(1)


if __name__ == "__main__":
    main()
