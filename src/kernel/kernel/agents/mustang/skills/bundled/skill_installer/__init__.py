"""Directory-backed bundled skill-installer registration."""

from __future__ import annotations

from pathlib import Path

from kernel.agents.mustang.skills.bundled import BundledSkillDef, register_bundled_skill
from kernel.agents.mustang.skills.manifest import parse_skill_manifest, strip_frontmatter

_ROOT = Path(__file__).parent


def _supporting_files() -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(_ROOT.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "__init__.py" or path.name == "SKILL.md":
            continue
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(_ROOT).as_posix()
        files[rel] = path.read_text(encoding="utf-8")
    return files


_manifest = parse_skill_manifest(_ROOT)
_body = strip_frontmatter((_ROOT / "SKILL.md").read_text(encoding="utf-8"))

_skill = register_bundled_skill(
    BundledSkillDef(
        name=_manifest.name,
        description=_manifest.description,
        when_to_use=_manifest.when_to_use,
        allowed_tools=_manifest.allowed_tools,
        argument_hint=_manifest.argument_hint,
        user_invocable=_manifest.user_invocable,
        disable_model_invocation=_manifest.disable_model_invocation,
        context=_manifest.context,
        agent=_manifest.agent,
        model=_manifest.model,
        body=_body,
        files=_supporting_files(),
    )
)

