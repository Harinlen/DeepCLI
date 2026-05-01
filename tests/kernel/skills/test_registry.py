"""SkillRegistry — registration, lookup, priority, pools."""

from __future__ import annotations

from pathlib import Path

from kernel.skills.registry import SkillRegistry
from kernel.skills.types import LoadedSkill, SkillManifest, SkillSource


def _skill(name: str, source: SkillSource = SkillSource.USER, priority: int = 2, **kw) -> LoadedSkill:
    manifest = SkillManifest(
        name=name,
        description=f"{name} skill",
        has_user_specified_description=True,
        base_dir=Path(f"/tmp/{name}"),
        **kw,
    )
    return LoadedSkill(
        manifest=manifest,
        source=source,
        layer_priority=priority,
        file_path=Path(f"/tmp/{name}/SKILL.md"),
    )


def test_register_and_lookup() -> None:
    reg = SkillRegistry()
    reg.register(_skill("a"))
    assert reg.lookup("a") is not None
    assert reg.lookup("b") is None


def test_priority_wins() -> None:
    reg = SkillRegistry()
    reg.register(_skill("x", SkillSource.PROJECT, priority=0))
    reg.register(_skill("x", SkillSource.USER, priority=2))
    assert reg.lookup("x").source == SkillSource.PROJECT


def test_lower_priority_does_not_override() -> None:
    reg = SkillRegistry()
    reg.register(_skill("x", SkillSource.PROJECT, priority=0))
    reg.register(_skill("x", SkillSource.USER, priority=2))
    assert reg.lookup("x").source == SkillSource.PROJECT


def test_dynamic_overrides_static() -> None:
    reg = SkillRegistry()
    reg.register(_skill("a", SkillSource.USER))
    reg.register_dynamic(_skill("a", SkillSource.PROJECT))
    assert reg.lookup("a").source == SkillSource.PROJECT


def test_all_skills_merges_pools() -> None:
    reg = SkillRegistry()
    reg.register(_skill("static"))
    reg.register_dynamic(_skill("dynamic"))
    names = {s.manifest.name for s in reg.all_skills()}
    assert names == {"static", "dynamic"}


def test_model_invocable_excludes_disabled() -> None:
    reg = SkillRegistry()
    reg.register(_skill("visible"))
    reg.register(_skill("hidden", disable_model_invocation=True))
    names = {s.manifest.name for s in reg.model_invocable()}
    assert names == {"visible"}


def test_user_invocable() -> None:
    reg = SkillRegistry()
    reg.register(_skill("public"))
    reg.register(_skill("private", user_invocable=False))
    names = {s.manifest.name for s in reg.user_invocable()}
    assert names == {"public"}


def test_conditional_activation() -> None:
    reg = SkillRegistry()
    cond = _skill("cond")
    reg.register_conditional(cond)
    assert reg.conditional_count() == 1
    assert reg.lookup("cond") is None  # Not visible yet.

    activated = reg.activate_conditional("cond")
    assert activated is not None
    assert reg.lookup("cond") is not None  # Now visible.
    assert reg.conditional_count() == 0


def test_clear() -> None:
    reg = SkillRegistry()
    reg.register(_skill("a"))
    reg.register_dynamic(_skill("b"))
    reg.register_conditional(_skill("c"))
    reg.clear()
    assert reg.all_skills() == []
    assert reg.conditional_count() == 0
