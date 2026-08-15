#!/usr/bin/env python3
"""Convert a human-readable datetime string to a UTC Unix timestamp.

Usage:
    python tools/to_unix.py "2026-08-15 14:30 Asia/Manila"
    python tools/to_unix.py "2026-08-15 00:00 UTC"

The output is an integer (UTC seconds since Unix epoch).
Requires Python 3.9+ (zoneinfo is stdlib).
"""

from __future__ import annotations

import sys
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_to_unix(dt_string: str) -> int:
    """Parse 'YYYY-MM-DD HH:MM TZ' and return UTC Unix seconds."""
    parts = dt_string.strip().rsplit(maxsplit=1)
    if len(parts) != 2:
        raise ValueError(
            f"Expected 'YYYY-MM-DD HH:MM Timezone', got {dt_string!r}\n"
            "Example: '2026-08-15 14:30 Asia/Manila'"
        )
    dt_part, tz_name = parts
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        raise ValueError(f"Unknown timezone: {tz_name!r}")

    try:
        dt = datetime.strptime(dt_part, "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError(
            f"Cannot parse datetime {dt_part!r}. Expected 'YYYY-MM-DD HH:MM'."
        )

    aware = dt.replace(tzinfo=tz)
    return int(aware.timestamp())


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if sys.argv[1:] else 1)

    try:
        ts = parse_to_unix(sys.argv[1])
        print(ts)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
