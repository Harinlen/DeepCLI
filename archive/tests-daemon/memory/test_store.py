"""Tests for MemoryStore — CRUD + path safety + cache + index sync."""

from pathlib import Path

import pytest

from daemon.memory.schema import (
    MemoryFrontmatter,
    MemoryKind,
    MemoryType,
)
from daemon.memory.store import (
    TYPE_DIRS,
    MemoryStore,
    MemoryStoreError,
    _append_to_section,
    _split_frontmatter,
)


def _fm(
    type: MemoryType, name: str = "x", desc: str = "y", kind: MemoryKind = MemoryKind.STANDALONE
) -> MemoryFrontmatter:
    return MemoryFrontmatter(name=name, description=desc, type=type, kind=kind)


class TestLifecycle:
    def test_load_creates_skeleton(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        for type_name in TYPE_DIRS:
            assert (tmp_path / "memory" / type_name).is_dir()
        assert (tmp_path / "memory" / "index.md").exists()

    def test_load_scans_existing_files(self, tmp_path: Path) -> None:
        root = tmp_path / "memory"
        root.mkdir()
        (root / "user").mkdir()
        (root / "user" / "role.md").write_text(
            "---\nname: role\ndescription: backend\ntype: user\nkind: standalone\n---\n\nbody text\n"
        )
        store = MemoryStore(root)
        store.load()
        recs = store.records()
        assert len(recs) == 1
        assert recs[0].frontmatter.name == "role"

    def test_load_skips_malformed_files(self, tmp_path: Path) -> None:
        root = tmp_path / "memory"
        root.mkdir()
        (root / "user").mkdir()
        (root / "user" / "bad.md").write_text("no frontmatter here")
        store = MemoryStore(root)
        store.load()  # should not raise
        assert store.records() == []


class TestWrite:
    def test_write_new_file(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        path = store.write(
            MemoryType.USER,
            "role.md",
            _fm(MemoryType.USER, "role", "backend engineer"),
            "This is the body.",
        )
        assert path.exists()
        text = path.read_text()
        assert "backend engineer" in text
        assert "This is the body." in text

    def test_write_updates_cache(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        store.write(MemoryType.USER, "role.md", _fm(MemoryType.USER, "role", "desc"), "b")
        recs = store.records()
        assert len(recs) == 1
        assert recs[0].relative == "user/role.md"

    def test_write_regenerates_index(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        store.write(MemoryType.USER, "role.md", _fm(MemoryType.USER, "role", "BE engineer"), "body")
        index = (tmp_path / "memory" / "index.md").read_text()
        assert "## User" in index
        assert "role.md" in index
        assert "BE engineer" in index

    def test_write_overwrite_logs_update(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        store.write(MemoryType.USER, "r.md", _fm(MemoryType.USER, "r", "v1"), "a")
        store.write(MemoryType.USER, "r.md", _fm(MemoryType.USER, "r", "v2"), "b")
        log = store.log.read()
        assert "WRITE" in log
        assert "UPDATE" in log

    def test_write_rewrites_frontmatter_type(self, tmp_path: Path) -> None:
        """If FM.type disagrees with type arg, type arg wins."""
        store = MemoryStore(tmp_path / "memory")
        store.load()
        # FM says FEEDBACK but we write into user/
        store.write(
            MemoryType.USER,
            "x.md",
            _fm(MemoryType.FEEDBACK, "x", "y"),
            "body",
        )
        fm, _ = store.read("user/x.md")
        assert fm.type == MemoryType.USER


class TestPathSafety:
    def test_rejects_slash_in_filename(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        with pytest.raises(MemoryStoreError):
            store.write(MemoryType.USER, "sub/x.md", _fm(MemoryType.USER), "b")

    def test_rejects_dotdot(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        with pytest.raises(MemoryStoreError):
            store.write(MemoryType.USER, "..x.md", _fm(MemoryType.USER), "b")

    def test_rejects_non_md_extension(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        with pytest.raises(MemoryStoreError):
            store.write(MemoryType.USER, "role.txt", _fm(MemoryType.USER), "b")

    def test_rejects_hidden_file(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        with pytest.raises(MemoryStoreError):
            store.write(MemoryType.USER, ".hidden.md", _fm(MemoryType.USER), "b")

    def test_rejects_empty_filename(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        with pytest.raises(MemoryStoreError):
            store.write(MemoryType.USER, "", _fm(MemoryType.USER), "b")


class TestAppend:
    def test_append_to_existing_section(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        body = "## Tools\n- pytest\n"
        store.write(
            MemoryType.USER,
            "prefs.md",
            _fm(MemoryType.USER, "prefs", "tools", MemoryKind.AGGREGATE),
            body,
        )
        store.append(MemoryType.USER, "prefs.md", "Tools", "ripgrep")
        _, new_body = store.read("user/prefs.md")
        assert "- pytest" in new_body
        assert "- ripgrep" in new_body

    def test_append_creates_new_section(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        body = "## Tools\n- pytest\n"
        store.write(
            MemoryType.USER,
            "prefs.md",
            _fm(MemoryType.USER, "prefs", "x", MemoryKind.AGGREGATE),
            body,
        )
        store.append(MemoryType.USER, "prefs.md", "Style", "tabs over spaces")
        _, new_body = store.read("user/prefs.md")
        assert "## Style" in new_body
        assert "- tabs over spaces" in new_body

    def test_append_rejects_standalone(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        store.write(
            MemoryType.USER,
            "role.md",
            _fm(MemoryType.USER, "role", "x", MemoryKind.STANDALONE),
            "body",
        )
        with pytest.raises(MemoryStoreError, match="kind=standalone"):
            store.append(MemoryType.USER, "role.md", "Tools", "pytest")

    def test_append_rejects_missing_file(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        with pytest.raises(MemoryStoreError, match="No such aggregate"):
            store.append(MemoryType.USER, "nope.md", "Tools", "pytest")

    def test_append_rejects_empty_bullet(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        store.write(
            MemoryType.USER,
            "p.md",
            _fm(MemoryType.USER, "p", "x", MemoryKind.AGGREGATE),
            "## A\n- x\n",
        )
        with pytest.raises(MemoryStoreError):
            store.append(MemoryType.USER, "p.md", "A", "   ")

    def test_append_rejects_empty_section(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        store.write(
            MemoryType.USER,
            "p.md",
            _fm(MemoryType.USER, "p", "x", MemoryKind.AGGREGATE),
            "## A\n- x\n",
        )
        with pytest.raises(MemoryStoreError):
            store.append(MemoryType.USER, "p.md", "   ", "bullet")

    def test_append_updates_index(self, tmp_path: Path) -> None:
        """Appending doesn't change description, but re-writes index anyway."""
        store = MemoryStore(tmp_path / "memory")
        store.load()
        store.write(
            MemoryType.USER,
            "p.md",
            _fm(MemoryType.USER, "p", "initial desc", MemoryKind.AGGREGATE),
            "## A\n- x\n",
        )
        store.append(MemoryType.USER, "p.md", "A", "y")
        index = (tmp_path / "memory" / "index.md").read_text()
        assert "initial desc" in index


class TestDelete:
    def test_delete_existing(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        store.write(MemoryType.USER, "x.md", _fm(MemoryType.USER), "b")
        assert store.delete(MemoryType.USER, "x.md") is True
        assert store.records() == []
        assert not (tmp_path / "memory" / "user" / "x.md").exists()

    def test_delete_missing(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        assert store.delete(MemoryType.USER, "nope.md") is False

    def test_delete_updates_index(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        store.write(MemoryType.USER, "x.md", _fm(MemoryType.USER, "x", "dd"), "b")
        store.delete(MemoryType.USER, "x.md")
        index = (tmp_path / "memory" / "index.md").read_text()
        assert "x.md" not in index

    def test_delete_logs(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        store.write(MemoryType.USER, "x.md", _fm(MemoryType.USER), "b")
        store.delete(MemoryType.USER, "x.md")
        log = store.log.read()
        assert "DELETE" in log


class TestRecordsFilter:
    def test_filter_by_type(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        store.write(MemoryType.USER, "u.md", _fm(MemoryType.USER), "b")
        store.write(MemoryType.FEEDBACK, "f.md", _fm(MemoryType.FEEDBACK), "b")
        user_only = store.records(MemoryType.USER)
        assert len(user_only) == 1
        assert user_only[0].relative == "user/u.md"


class TestReadRoundtrip:
    def test_write_then_read(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        store.write(
            MemoryType.FEEDBACK,
            "tests.md",
            _fm(MemoryType.FEEDBACK, "tests", "real DB only"),
            "integration tests must hit real DB",
        )
        fm, body = store.read("feedback/tests.md")
        assert fm.name == "tests"
        assert "real DB only" in fm.description
        assert "integration tests" in body

    def test_read_missing(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        with pytest.raises(MemoryStoreError):
            store.read("user/nope.md")

    def test_read_rejects_bad_relative(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        with pytest.raises(MemoryStoreError):
            store.read("notatype/x.md")


class TestPersistenceRoundtrip:
    def test_load_sees_written_files(self, tmp_path: Path) -> None:
        """Write with store A, instantiate fresh store B, B should see files."""
        root = tmp_path / "memory"
        store_a = MemoryStore(root)
        store_a.load()
        store_a.write(MemoryType.USER, "role.md", _fm(MemoryType.USER, "role", "be"), "body")
        store_b = MemoryStore(root)
        store_b.load()
        recs = store_b.records()
        assert len(recs) == 1
        assert recs[0].frontmatter.name == "role"


class TestHelpers:
    def test_split_frontmatter_valid(self) -> None:
        text = "---\nname: x\n---\n\nbody\n"
        fm, body = _split_frontmatter(text)
        assert fm is not None
        assert "name: x" in fm
        assert body.strip() == "body"

    def test_split_frontmatter_missing_delim(self) -> None:
        fm, body = _split_frontmatter("no delimiters here")
        assert fm is None
        assert body == "no delimiters here"

    def test_append_to_section_existing(self) -> None:
        body = "## Tools\n- a\n- b\n"
        new = _append_to_section(body, "Tools", "c")
        assert "- c" in new
        assert new.count("- ") == 3

    def test_append_to_section_new_section(self) -> None:
        body = "## Tools\n- a\n"
        new = _append_to_section(body, "Style", "tabs")
        assert "## Style" in new
        assert "- tabs" in new

    def test_append_to_section_empty_body(self) -> None:
        new = _append_to_section("", "Tools", "a")
        assert "## Tools" in new
        assert "- a" in new

    def test_append_preserves_later_section(self) -> None:
        body = "## A\n- x\n\n## B\n- y\n"
        new = _append_to_section(body, "A", "z")
        # z should land in A, not at the end
        a_pos = new.index("## A")
        z_pos = new.index("- z")
        b_pos = new.index("## B")
        assert a_pos < z_pos < b_pos
