#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import hash_tree, out, read_manifest  # type: ignore[import-not-found]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--url")
    parser.add_argument("--repo")
    parser.add_argument("--path")
    parser.add_argument("--ref")
    parser.add_argument("--layer", default="project")
    args = parser.parse_args()
    manifest = read_manifest(args.target)
    payload = {
        "schemaVersion": 1,
        "installedBy": "skill-installer",
        "source": {
            "kind": args.kind,
            "url": args.url,
            "repo": args.repo,
            "path": args.path,
            "ref": args.ref,
            "contentHash": hash_tree(args.target),
        },
        "target": {
            "layer": args.layer,
            "path": str(Path(args.target).resolve()),
            "skillName": manifest["name"],
        },
        "warnings": [],
    }
    (Path(args.target) / ".deepcli-skill-source.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    out(
        {
            "ok": True,
            "action": "provenance",
            "skillName": manifest["name"],
            "targetPath": str(Path(args.target).resolve()),
            "warnings": [],
        }
    )


if __name__ == "__main__":
    main()

