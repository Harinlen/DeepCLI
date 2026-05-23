from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path


def out(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def fail(action: str, code: str, message: str) -> None:
    print(
        json.dumps(
            {"ok": False, "action": action, "error": code, "message": message},
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    raise SystemExit(1)


def read_manifest(skill_dir: str | Path) -> dict[str, str]:
    path = Path(skill_dir) / "SKILL.md"
    if not path.is_file():
        fail("validate", "missing_manifest", f"missing SKILL.md in {skill_dir}")
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        fail("validate", "malformed_manifest", "SKILL.md must start with YAML frontmatter")
    end = text.find("\n---", 4)
    if end < 0:
        fail("validate", "malformed_manifest", "SKILL.md frontmatter is not closed")
    name = None
    for line in text[4:end].splitlines():
        if line.strip().startswith("name:"):
            name = line.split(":", 1)[1].strip().strip('"\'')
            break
    return {"name": name or Path(skill_dir).name, "path": str(path)}


def ensure_safe_tree(src: str | Path) -> None:
    root = Path(src).resolve()
    for child in root.rglob("*"):
        try:
            resolved = child.resolve()
        except OSError as exc:
            fail("validate", "invalid_path", str(exc))
        if root not in (resolved, *resolved.parents):
            fail("validate", "symlink_escape", str(child))
        if ".." in child.relative_to(root).parts:
            fail("validate", "path_traversal", str(child))


def copy_skill(src: str | Path, dest: str | Path, force: bool = False) -> Path:
    src_path = Path(src).resolve()
    dest_path = Path(dest).resolve()
    if not src_path.is_dir():
        fail("install", "source_not_directory", str(src_path))
    if src_path == dest_path or src_path in dest_path.parents or dest_path in src_path.parents:
        fail("install", "unsafe_source_target_overlap", f"{src_path} -> {dest_path}")
    ensure_safe_tree(src_path)
    if dest_path.exists():
        if not force:
            fail("install", "target_exists", str(dest_path))
        shutil.rmtree(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_path, dest_path, symlinks=False)
    return dest_path


def hash_tree(path: str | Path) -> str:
    digest = hashlib.sha256()
    root = Path(path)
    for child in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(child.relative_to(root)).encode())
        digest.update(child.read_bytes())
    return "sha256:" + digest.hexdigest()

