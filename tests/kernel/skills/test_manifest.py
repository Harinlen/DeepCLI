"""SKILL.md frontmatter parsing — happy path + error modes."""

from __future__ import annotations

from pathlib import Path

import pytest

from kernel.skills.manifest import ManifestError, parse_skill_manifest, strip_frontmatter


def _write_skill(
    base: Path,
    *,
    name: str = "demo",
    md: str | None = None,
) -> Path:
    """Create a skill directory with SKILL.md."""
    skill_dir = base / name
    skill_dir.mkdir()
    if md is None:
        md = (
            "---\n"
            f"name: {name}\n"
            "description: A test skill\n"
            "---\n"
            "# Demo Skill\n\n"
            "Body text here.\n"
        )
    (skill_dir / "SKILL.md").write_text(md, encoding="utf-8")
    return skill_dir


# -- Happy path --


def test_minimal_manifest_parses(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path)
    m = parse_skill_manifest(skill_dir)

    assert m.name == "demo"
    assert m.description == "A test skill"
    assert m.has_user_specified_description is True
    assert m.allowed_tools == ()
    assert m.user_invocable is True
    assert m.disable_model_invocation is False
    assert m.base_dir == skill_dir


def test_name_defaults_to_directory(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        name="my-skill",
        md="---\ndescription: test\n---\n# body\n",
    )
    m = parse_skill_manifest(skill_dir)
    assert m.name == "my-skill"


def test_description_fallback_from_heading(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        md="---\nname: x\n---\n# My Great Skill\n\nBody.\n",
    )
    m = parse_skill_manifest(skill_dir)
    assert m.description == "My Great Skill"
    assert m.has_user_specified_description is False


def test_description_fallback_from_paragraph(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        md="---\nname: x\n---\nFirst paragraph text.\n",
    )
    m = parse_skill_manifest(skill_dir)
    assert m.description == "First paragraph text."


def test_full_frontmatter(tmp_path: Path) -> None:
    md = (
        "---\n"
        "name: full-skill\n"
        "description: Full test\n"
        "allowed-tools:\n"
        "  - Bash(npm run *)\n"
        "  - Grep\n"
        "argument-hint: <url>\n"
        "arguments: [url, format]\n"
        "when-to-use: When testing\n"
        "user-invocable: false\n"
        "disable-model-invocation: true\n"
        "requires:\n"
        "  bins: [git]\n"
        "  env: [API_KEY]\n"
        "  tools: [Bash]\n"
        "  toolsets: [mcp_github]\n"
        "fallback-for:\n"
        "  tools: [WebSearch]\n"
        "os: [linux, darwin]\n"
        "context: fork\n"
        "agent: general-purpose\n"
        "model: opus\n"
        "paths:\n"
        "  - src/api/**\n"
        "setup:\n"
        "  env:\n"
        "    - name: MY_KEY\n"
        "      prompt: Enter key\n"
        "      secret: true\n"
        "config:\n"
        "  retries: 3\n"
        "---\n"
        "# Body\n"
    )
    skill_dir = _write_skill(tmp_path, name="full", md=md)
    m = parse_skill_manifest(skill_dir)

    assert m.name == "full-skill"
    assert m.allowed_tools == ("Bash(npm run *)", "Grep")
    assert m.argument_hint == "<url>"
    assert m.argument_names == ("url", "format")
    assert m.when_to_use == "When testing"
    assert m.user_invocable is False
    assert m.disable_model_invocation is True
    assert m.requires.bins == ("git",)
    assert m.requires.env == ("API_KEY",)
    assert m.requires.tools == ("Bash",)
    assert m.requires.toolsets == ("mcp_github",)
    assert m.fallback_for is not None
    assert m.fallback_for.tools == ("WebSearch",)
    assert m.os == ("linux", "darwin")
    assert m.context == "fork"
    assert m.agent == "general-purpose"
    assert m.model == "opus"
    assert m.paths == ("src/api/**",)
    assert m.setup is not None
    assert len(m.setup.env) == 1
    assert m.setup.env[0].name == "MY_KEY"
    assert m.setup.env[0].secret is True
    assert m.config == {"retries": 3}


def test_unknown_keys_silently_dropped(tmp_path: Path) -> None:
    md = "---\nname: x\ndescription: t\nfuture_field: 42\n---\n# body\n"
    skill_dir = _write_skill(tmp_path, md=md)
    m = parse_skill_manifest(skill_dir)
    assert m.name == "x"


def test_supporting_files_discovered(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path)
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "api.md").write_text("API docs")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "setup.sh").write_text("#!/bin/sh")
    m = parse_skill_manifest(skill_dir)
    assert "references/api.md" in m.supporting_files
    assert "scripts/setup.sh" in m.supporting_files


def test_when_to_use_snake_case(tmp_path: Path) -> None:
    md = "---\nname: x\ndescription: t\nwhen_to_use: snake case\n---\n# body\n"
    skill_dir = _write_skill(tmp_path, md=md)
    m = parse_skill_manifest(skill_dir)
    assert m.when_to_use == "snake case"


# -- Error modes --


def test_missing_skill_md(tmp_path: Path) -> None:
    skill_dir = tmp_path / "no-skill"
    skill_dir.mkdir()
    with pytest.raises(ManifestError, match="missing SKILL.md"):
        parse_skill_manifest(skill_dir)


def test_no_frontmatter_fence(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, md="# No frontmatter\nBody.\n")
    with pytest.raises(ManifestError, match="expected '---'"):
        parse_skill_manifest(skill_dir)


def test_unclosed_frontmatter(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, md="---\nname: x\n# no close\n")
    with pytest.raises(ManifestError, match="closing '---' not found"):
        parse_skill_manifest(skill_dir)


def test_yaml_parse_error(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, md="---\n: invalid yaml [[\n---\n# body\n")
    with pytest.raises(ManifestError, match="YAML parse error"):
        parse_skill_manifest(skill_dir)


# -- strip_frontmatter --


def test_strip_frontmatter_basic() -> None:
    text = "---\nname: x\n---\n# Body\nContent.\n"
    body = strip_frontmatter(text)
    assert body.startswith("# Body")


def test_strip_frontmatter_no_fence() -> None:
    text = "# No frontmatter\nContent.\n"
    assert strip_frontmatter(text) == text
