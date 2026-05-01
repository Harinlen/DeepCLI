"""Tests for memory frontmatter schema."""

import pytest
from pydantic import ValidationError

from daemon.memory.schema import (
    MemoryFrontmatter,
    MemoryKind,
    MemoryType,
)


class TestMemoryFrontmatter:
    def test_minimal_valid(self) -> None:
        fm = MemoryFrontmatter(
            name="role",
            description="backend engineer",
            type=MemoryType.USER,
        )
        assert fm.kind == MemoryKind.STANDALONE  # default

    def test_aggregate_kind(self) -> None:
        fm = MemoryFrontmatter(
            name="prefs",
            description="pytest, tabs, ripgrep",
            type=MemoryType.USER,
            kind=MemoryKind.AGGREGATE,
        )
        assert fm.kind == MemoryKind.AGGREGATE

    def test_name_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryFrontmatter(name="", description="x", type=MemoryType.USER)

    def test_description_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryFrontmatter(name="x", description="", type=MemoryType.USER)

    def test_description_max_length(self) -> None:
        with pytest.raises(ValidationError):
            MemoryFrontmatter(
                name="x",
                description="x" * 301,
                type=MemoryType.USER,
            )

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            MemoryFrontmatter(
                name="x",
                description="y",
                type=MemoryType.USER,
                extra_field="boom",  # type: ignore[call-arg]
            )

    def test_type_string_value(self) -> None:
        fm = MemoryFrontmatter(name="x", description="y", type="user")
        assert fm.type == MemoryType.USER

    def test_invalid_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryFrontmatter(name="x", description="y", type="nonsense")
