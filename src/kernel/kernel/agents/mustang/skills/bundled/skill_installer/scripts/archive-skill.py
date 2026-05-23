#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from common import fail, out, read_manifest  # type: ignore[import-not-found]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("--archive-root", required=True)
    args = parser.parse_args()
    manifest = read_manifest(args.target)
    source = Path(args.target).resolve()
    archive_root = Path(args.archive_root).resolve()
    if ".archive" in source.parts:
        fail("archive", "already_archived", str(source))
    dest = archive_root / (manifest["name"] + "-archived")
    archive_root.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        fail("archive", "archive_exists", str(dest))
    shutil.move(str(source), str(dest))
    (dest / ".deepcli-skill-archive.json").write_text(
        json.dumps(
            {
                "skillName": manifest["name"],
                "originalPath": str(source),
                "reason": "user-requested",
                "source": "deepcli",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    out(
        {
            "ok": True,
            "action": "archive",
            "skillName": manifest["name"],
            "targetPath": str(dest),
            "warnings": [],
        }
    )


if __name__ == "__main__":
    main()

