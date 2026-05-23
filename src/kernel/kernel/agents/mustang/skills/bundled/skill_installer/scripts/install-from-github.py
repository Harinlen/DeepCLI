#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from common import copy_skill, fail, hash_tree, out, read_manifest  # type: ignore[import-not-found]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("--path", default="")
    parser.add_argument("--ref")
    parser.add_argument("--dest", required=True)
    parser.add_argument("--name")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "repo"
        cmd = ["git", "clone", "--depth", "1"]
        if args.ref:
            cmd += ["--branch", args.ref]
        cmd += [f"https://github.com/{args.repo}.git", str(clone)]
        result = subprocess.run(cmd, text=True, capture_output=True)
        if result.returncode != 0:
            fail("install", "git_clone_failed", result.stderr.strip() or result.stdout.strip())
        source = clone / args.path if args.path else clone
        manifest = read_manifest(source)
        if args.name and args.name != manifest["name"]:
            fail(
                "install",
                "name_mismatch",
                f"--name {args.name} does not match manifest {manifest['name']}",
            )
        dest = copy_skill(source, args.dest, args.force)
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

