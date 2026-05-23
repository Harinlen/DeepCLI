#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from common import hash_tree, out, read_manifest  # type: ignore[import-not-found]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    args = parser.parse_args()
    manifest = read_manifest(args.target)
    provenance = Path(args.target) / ".deepcli-skill-source.json"
    warnings = [] if provenance.is_file() else ["missing_provenance"]
    out(
        {
            "ok": True,
            "action": "check",
            "skillName": manifest["name"],
            "targetPath": str(Path(args.target).resolve()),
            "contentHash": hash_tree(args.target),
            "warnings": warnings,
        }
    )


if __name__ == "__main__":
    main()

