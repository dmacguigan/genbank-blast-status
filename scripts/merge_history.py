#!/usr/bin/env python3
"""Merge two status-history files into one, deduped by timestamp.

Used by the CI commit step to reconcile this run's history with whatever
landed on the remote concurrently, so pushes never hit a JSON merge conflict.

Usage: merge_history.py REMOTE MINE OUT [LIMIT]
  REMOTE  history.json currently on the branch tip
  MINE    history.json this run produced
  OUT     path to write the merged result
  LIMIT   max entries to keep (default 336)
"""

import json
import sys

DEFAULT_LIMIT = 336


def load(path):
    try:
        data = json.loads(open(path).read())
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def main(argv):
    remote, mine, out = argv[1], argv[2], argv[3]
    limit = int(argv[4]) if len(argv) > 4 else DEFAULT_LIMIT
    by_ts = {}
    for entry in load(remote) + load(mine):
        ts = entry.get("t")
        if ts:
            by_ts[ts] = entry  # later (ours) wins ties
    merged = [by_ts[ts] for ts in sorted(by_ts)][-limit:]
    with open(out, "w") as fh:
        fh.write(json.dumps(merged, indent=0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
