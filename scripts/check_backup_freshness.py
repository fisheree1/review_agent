"""Exit nonzero when the last encrypted production snapshot is too old."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta


def latest_snapshot_age(snapshot_json: bytes, *, now: datetime) -> timedelta:
    snapshots = json.loads(snapshot_json)
    timestamps = [
        datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
        for item in snapshots
        if "review-agent-production" in item.get("tags", [])
    ]
    if not timestamps:
        raise ValueError("No production backup snapshot exists")
    return now - max(timestamps)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-age-hours", type=int, default=30)
    args = parser.parse_args()
    if args.max_age_hours < 1:
        parser.error("--max-age-hours must be positive")
    result = subprocess.run(
        ["restic", "snapshots", "--json", "--tag", "review-agent-production"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print("Backup repository cannot be read", file=sys.stderr)
        raise SystemExit(1)
    try:
        age = latest_snapshot_age(result.stdout, now=datetime.now(UTC))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"Backup freshness check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if age > timedelta(hours=args.max_age_hours) or age < timedelta(0):
        print("Latest production backup is outside the allowed age", file=sys.stderr)
        raise SystemExit(1)
    print(f"Production backup age is {age.total_seconds() / 3600:.1f} hours.")


if __name__ == "__main__":
    main()
