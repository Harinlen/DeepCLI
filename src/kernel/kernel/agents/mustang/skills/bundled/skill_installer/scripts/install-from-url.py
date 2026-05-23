#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
import urllib.request
from pathlib import Path

from common import copy_skill, fail, hash_tree, out, read_manifest  # type: ignore[import-not-found]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--dest", required=True)
    parser.add_argument("--name")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / (args.name or "downloaded-skill")
        skill_dir.mkdir()
        try:
            data = urllib.request.urlopen(args.url, timeout=30).read()
        except Exception as exc:
            fail("install", "download_failed", str(exc))
        (skill_dir / "SKILL.md").write_bytes(data)
        manifest = read_manifest(skill_dir)
        if args.name and args.name != manifest["name"]:
            fail(
                "install",
                "name_mismatch",
                f"--name {args.name} does not match manifest {manifest['name']}",
            )
        dest = copy_skill(skill_dir, args.dest, args.force)
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

