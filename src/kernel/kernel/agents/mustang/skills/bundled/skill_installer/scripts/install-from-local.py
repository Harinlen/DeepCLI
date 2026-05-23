#!/usr/bin/env python3
from __future__ import annotations

import argparse

from common import copy_skill, fail, hash_tree, out, read_manifest  # type: ignore[import-not-found]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--dest", required=True)
    parser.add_argument("--name")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    manifest = read_manifest(args.source)
    if args.name and args.name != manifest["name"]:
        fail(
            "install",
            "name_mismatch",
            f"--name {args.name} does not match manifest {manifest['name']}",
        )
    dest = copy_skill(args.source, args.dest, args.force)
    out(
        {
            "ok": True,
            "action": "install",
            "skillName": manifest["name"],
            "targetPath": str(dest),
            "contentHash": hash_tree(dest),
            "warnings": [],
        }
    )


if __name__ == "__main__":
    main()

