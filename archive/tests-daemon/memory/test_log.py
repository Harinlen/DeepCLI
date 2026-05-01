"""Tests for MemoryLog append + rotation."""

from pathlib import Path

from daemon.memory.log import MemoryLog


class TestMemoryLogAppend:
    def test_append_creates_file(self, tmp_path: Path) -> None:
        log = MemoryLog(tmp_path / "log.md")
        log.append("WRITE", "user/role.md")
        content = (tmp_path / "log.md").read_text()
        assert "WRITE" in content
        assert "user/role.md" in content

    def test_append_with_note(self, tmp_path: Path) -> None:
        log = MemoryLog(tmp_path / "log.md")
        log.append("APPEND", "user/prefs.md", "tools pytest added")
        content = log.read()
        assert "tools pytest added" in content
        assert " — " in content

    def test_append_no_note_no_separator(self, tmp_path: Path) -> None:
        log = MemoryLog(tmp_path / "log.md")
        log.append("DELETE", "user/stale.md")
        content = log.read()
        assert " — " not in content

    def test_multiple_appends_ordered(self, tmp_path: Path) -> None:
        log = MemoryLog(tmp_path / "log.md")
        log.append("WRITE", "a.md")
        log.append("WRITE", "b.md")
        log.append("DELETE", "a.md")
        lines = log.read().splitlines()
        assert len(lines) == 3
        assert "a.md" in lines[0]
        assert "b.md" in lines[1]
        assert "DELETE" in lines[2]

    def test_unknown_op_still_writes(self, tmp_path: Path) -> None:
        """Unknown ops log a warning but do not raise."""
        log = MemoryLog(tmp_path / "log.md")
        log.append("WEIRD", "x")
        assert "WEIRD" in log.read()


class TestMemoryLogRotation:
    def test_rotation_triggers_at_threshold(self, tmp_path: Path) -> None:
        log = MemoryLog(tmp_path / "log.md")
        # MAX_LOG_LINES=200 — write 201 entries
        for i in range(201):
            log.append("WRITE", f"file{i}.md")
        # log.md truncated, archive created
        archive = tmp_path / "log.archive.md"
        assert archive.exists()
        archive_lines = archive.read_text().splitlines()
        # Archive has the first 201 entries (rotated after 201st write)
        assert len(archive_lines) >= 200

    def test_rotation_appends_to_existing_archive(self, tmp_path: Path) -> None:
        """Second rotation appends, never overwrites."""
        log = MemoryLog(tmp_path / "log.md")
        # First rotation
        for i in range(201):
            log.append("WRITE", f"a{i}.md")
        first_archive_size = (tmp_path / "log.archive.md").stat().st_size
        # Second rotation
        for i in range(201):
            log.append("WRITE", f"b{i}.md")
        second_archive_size = (tmp_path / "log.archive.md").stat().st_size
        assert second_archive_size > first_archive_size

    def test_log_md_truncated_after_rotation(self, tmp_path: Path) -> None:
        log = MemoryLog(tmp_path / "log.md")
        for i in range(201):
            log.append("WRITE", f"x{i}.md")
        # log.md should now be empty (or near-empty after truncate)
        assert log.read() == ""
