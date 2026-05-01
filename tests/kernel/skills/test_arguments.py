"""Argument + config substitution tests."""

from __future__ import annotations

from pathlib import Path

from kernel.skills.arguments import substitute_arguments, substitute_config


def test_arguments_positional() -> None:
    result = substitute_arguments("run $ARGUMENTS here", "foo bar", ())
    assert result == "run foo bar here"


def test_named_arguments() -> None:
    result = substitute_arguments(
        "fetch ${url} as ${format}", "https://example.com json", ("url", "format")
    )
    assert result == "fetch https://example.com as json"


def test_named_argument_missing() -> None:
    result = substitute_arguments(
        "use ${url} and ${format}", "only-url", ("url", "format")
    )
    assert result == "use only-url and "


def test_skill_dir_substitution() -> None:
    result = substitute_arguments(
        "read ${SKILL_DIR}/data.json", "", (), skill_dir=Path("/skills/my-skill")
    )
    assert result == "read /skills/my-skill/data.json"


def test_claude_skill_dir_compat() -> None:
    result = substitute_arguments(
        "read ${CLAUDE_SKILL_DIR}/data.json", "", (), skill_dir=Path("/skills/x")
    )
    assert result == "read /skills/x/data.json"


def test_config_substitution() -> None:
    result = substitute_config(
        "retries: ${config.max_retries}, fmt: ${config.output}",
        {"max_retries": 5, "output": "json"},
    )
    assert result == "retries: 5, fmt: json"


def test_config_unknown_key_preserved() -> None:
    result = substitute_config(
        "use ${config.unknown_key}", {"other": "val"}
    )
    assert result == "use ${config.unknown_key}"


def test_config_empty() -> None:
    result = substitute_config("no ${config.x} change", {})
    assert result == "no ${config.x} change"
