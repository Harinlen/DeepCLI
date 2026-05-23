#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("source")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
payload = json.loads((root / "references" / "skill-sources.json").read_text(encoding="utf-8"))
for item in payload.get("sources", []):
    if item.get("id") == args.source:
        print(json.dumps({"ok": True, "action": "resolve", "source": item}, sort_keys=True))
        raise SystemExit(0)
print(
    json.dumps(
        {
            "ok": False,
            "action": "resolve",
            "error": "unknown_source",
            "message": args.source,
        },
        sort_keys=True,
    ),
    file=sys.stderr,
)
raise SystemExit(1)

