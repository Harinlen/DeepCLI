#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from common import ensure_safe_tree, out, read_manifest  # type: ignore[import-not-found]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args()
    manifest = read_manifest(args.path)
    ensure_safe_tree(args.path)
    out(
        {
            "ok": True,
            "action": "validate",
            "skillName": manifest["name"],
            "targetPath": str(Path(args.path).resolve()),
            "warnings": [],
        }
    )


if __name__ == "__main__":
    main()

