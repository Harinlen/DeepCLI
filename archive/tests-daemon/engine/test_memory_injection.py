"""Tests for memory index + MEMORY_INSTRUCTIONS injection into system prompt."""

from pathlib import Path

from daemon.engine.context import PromptSection, build_system_prompt, prompt_sections_to_text
from daemon.engine.memory_prompt import MEMORY_INSTRUCTIONS
from daemon.memory.schema import MemoryFrontmatter, MemoryType
from daemon.memory.store import MemoryStore


class TestMemoryInjection:
    def _text(self, sections: list[PromptSection]) -> str:
        return prompt_sections_to_text(sections)

    def test_no_memory_index_omits_section(self, tmp_path: Path) -> None:
        prompt = self._text(build_system_prompt(cwd=tmp_path, memory_index=None))
        assert "# Memory index" not in prompt
        # Instructions also skipped when there's no index
        assert "merge-first" not in prompt

    def test_empty_memory_still_injects(self, tmp_path: Path) -> None:
        """An empty index is still a signal to the LLM."""
        store = MemoryStore(tmp_path / "memory")
        store.load()
        prompt = self._text(build_system_prompt(cwd=tmp_path, memory_index=store.index_text()))
        assert "# Memory index" in prompt
        assert "# Memory Index" in prompt  # rendered index heading
        assert "merge-first" in prompt.lower() or "merge" in prompt.lower()

    def test_populated_memory_appears_in_prompt(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "memory")
        store.load()
        store.write(
            MemoryType.USER,
            "role.md",
            MemoryFrontmatter(name="role", description="backend engineer", type=MemoryType.USER),
            "body",
        )
        prompt = self._text(build_system_prompt(cwd=tmp_path, memory_index=store.index_text()))
        assert "role.md" in prompt
        assert "backend engineer" in prompt

    def test_instructions_content(self, tmp_path: Path) -> None:
        prompt = self._text(build_system_prompt(cwd=tmp_path, memory_index="# Memory Index\n"))
        # Core rules that must survive into the prompt
        assert MEMORY_INSTRUCTIONS.strip() in prompt
        assert "Never use `file_edit`" in prompt
        assert "cross-project long-term" in prompt

    def test_memory_instructions_cacheable(self, tmp_path: Path) -> None:
        """MEMORY_INSTRUCTIONS section should be marked cacheable."""
        sections = build_system_prompt(cwd=tmp_path, memory_index="# Memory Index\n")
        memory_instr = [s for s in sections if "cross-project long-term" in s.text]
        assert len(memory_instr) == 1
        assert memory_instr[0].cacheable is True

    def test_memory_index_not_cacheable(self, tmp_path: Path) -> None:
        """Memory index content is dynamic — not cacheable."""
        sections = build_system_prompt(cwd=tmp_path, memory_index="# Memory Index\n\n## User\n")
        index_section = [s for s in sections if "# Memory index" in s.text]
        assert len(index_section) == 1
        assert index_section[0].cacheable is False

    def test_memory_section_placement(self, tmp_path: Path) -> None:
        """Memory section appears after AGENTS.md, before plan mode."""
        (tmp_path / "AGENTS.md").write_text("# Project rules\n")
        prompt = self._text(
            build_system_prompt(cwd=tmp_path, memory_index="# Memory Index\n\n## User\n- x\n")
        )
        agents_pos = prompt.index("Project rules")
        memory_pos = prompt.index("# Memory index")
        assert agents_pos < memory_pos
