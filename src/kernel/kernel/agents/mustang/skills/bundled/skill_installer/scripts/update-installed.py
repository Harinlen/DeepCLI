#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from common import fail, out, read_manifest  # type: ignore[import-not-found]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    manifest = read_manifest(args.target)
    provenance = Path(args.target) / ".deepcli-skill-source.json"
    if not provenance.is_file():
        fail("update", "missing_provenance", "update requires .deepcli-skill-source.json")
    out(
        {
            "ok": True,
            "action": "update",
            "skillName": manifest["name"],
            "targetPath": str(Path(args.target).resolve()),
            "warnings": ["noop_update_helper"],
        }
    )


if __name__ == "__main__":
    main()

