"""Tests for index.md generation."""

from pathlib import Path

from daemon.memory.index_gen import render_index
from daemon.memory.schema import (
    MemoryFrontmatter,
    MemoryKind,
    MemoryRecord,
    MemoryType,
)


def _rec(
    relative: str,
    name: str,
    description: str,
    type: MemoryType,
    kind: MemoryKind = MemoryKind.STANDALONE,
) -> MemoryRecord:
    return MemoryRecord(
        relative=relative,
        path=Path("/fake") / relative,
        frontmatter=MemoryFrontmatter(name=name, description=description, type=type, kind=kind),
        size_bytes=100,
    )


class TestRenderIndex:
    def test_empty(self) -> None:
        text = render_index([])
        assert text.startswith("# Memory Index")
        assert "## User" not in text

    def test_single_entry(self) -> None:
        records = [_rec("user/role.md", "role", "backend engineer", MemoryType.USER)]
        text = render_index(records)
        assert "## User" in text
        assert "[role.md](user/role.md) — backend engineer" in text

    def test_type_order_fixed(self) -> None:
        """User before Feedback before Project before Reference."""
        records = [
            _rec("reference/r.md", "r", "r", MemoryType.REFERENCE),
            _rec("project/p.md", "p", "p", MemoryType.PROJECT),
            _rec("feedback/f.md", "f", "f", MemoryType.FEEDBACK),
            _rec("user/u.md", "u", "u", MemoryType.USER),
        ]
        text = render_index(records)
        user_pos = text.index("## User")
        fb_pos = text.index("## Feedback")
        proj_pos = text.index("## Project")
        ref_pos = text.index("## Reference")
        assert user_pos < fb_pos < proj_pos < ref_pos

    def test_alphabetical_within_section(self) -> None:
        records = [
            _rec("user/zebra.md", "zebra", "z", MemoryType.USER),
            _rec("user/alpha.md", "alpha", "a", MemoryType.USER),
            _rec("user/mid.md", "mid", "m", MemoryType.USER),
        ]
        text = render_index(records)
        alpha_pos = text.index("alpha.md")
        mid_pos = text.index("mid.md")
        zebra_pos = text.index("zebra.md")
        assert alpha_pos < mid_pos < zebra_pos

    def test_empty_sections_omitted(self) -> None:
        records = [_rec("user/x.md", "x", "y", MemoryType.USER)]
        text = render_index(records)
        assert "## Feedback" not in text
        assert "## Project" not in text
        assert "## Reference" not in text

    def test_truncation_at_max_lines(self) -> None:
        # 150 entries → more than 200 lines after headers/blanks
        records = [
            _rec(
                f"user/e{i:03d}.md",
                f"entry{i:03d}",
                "x",
                MemoryType.USER,
            )
            for i in range(250)
        ]
        text = render_index(records)
        assert "more entries" in text
