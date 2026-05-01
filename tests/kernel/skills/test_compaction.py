"""Compaction preservation — create_skill_attachment."""

from __future__ import annotations

from unittest.mock import MagicMock

from kernel.orchestrator.compactor import create_skill_attachment
from kernel.skills.types import InvokedSkillInfo


def _mock_skills_manager(
    invoked: list[InvokedSkillInfo],
) -> MagicMock:
    mgr = MagicMock()
    mgr.get_invoked_for_agent.return_value = sorted(
        invoked, key=lambda s: s.invoked_at, reverse=True
    )
    return mgr


def test_no_invoked_returns_none() -> None:
    mgr = _mock_skills_manager([])
    result = create_skill_attachment(mgr)
    assert result is None


def test_single_invoked_skill() -> None:
    mgr = _mock_skills_manager([
        InvokedSkillInfo(
            skill_name="my-skill",
            skill_path="/tmp/my-skill/SKILL.md",
            content="Skill body content here",
            invoked_at=1000.0,
        ),
    ])
    result = create_skill_attachment(mgr)
    assert result is not None
    assert "my-skill" in result
    assert "Skill body content here" in result
    assert "previously invoked" in result


def test_multiple_skills_ordered_by_recency() -> None:
    mgr = _mock_skills_manager([
        InvokedSkillInfo(
            skill_name="old",
            skill_path="/tmp/old/SKILL.md",
            content="Old content",
            invoked_at=1000.0,
        ),
        InvokedSkillInfo(
            skill_name="new",
            skill_path="/tmp/new/SKILL.md",
            content="New content",
            invoked_at=2000.0,
        ),
    ])
    result = create_skill_attachment(mgr)
    assert result is not None
    # "new" should appear before "old" (descending invoked_at).
    new_pos = result.index("new")
    old_pos = result.index("old")
    assert new_pos < old_pos


def test_per_skill_truncation() -> None:
    # Create a skill with body larger than the per-skill budget (5000 tokens * 4 chars).
    large_body = "x" * 30_000
    mgr = _mock_skills_manager([
        InvokedSkillInfo(
            skill_name="big",
            skill_path="/tmp/big/SKILL.md",
            content=large_body,
            invoked_at=1000.0,
        ),
    ])
    result = create_skill_attachment(mgr)
    assert result is not None
    assert "truncated for compaction" in result
    assert len(result) < len(large_body)


def test_total_budget_respected() -> None:
    # Create many skills that together exceed the total budget.
    skills = [
        InvokedSkillInfo(
            skill_name=f"skill-{i}",
            skill_path=f"/tmp/skill-{i}/SKILL.md",
            content="y" * 15_000,  # Each ~15K chars
            invoked_at=float(1000 + i),
        )
        for i in range(20)
    ]
    mgr = _mock_skills_manager(skills)
    result = create_skill_attachment(mgr)
    assert result is not None
    # Total budget is 25000 tokens * 4 chars = 100K chars.
    # 20 skills * 15K = 300K > budget, so some should be dropped.
    assert result.count("<skill") < 20


def test_none_skills_manager() -> None:
    result = create_skill_attachment(None)
    assert result is None


def test_agent_id_filtering() -> None:
    mgr = _mock_skills_manager([
        InvokedSkillInfo(
            skill_name="agent-skill",
            skill_path="/tmp/a/SKILL.md",
            content="Agent content",
            invoked_at=1000.0,
            agent_id="agent-1",
        ),
    ])
    result = create_skill_attachment(mgr, agent_id="agent-1")
    assert result is not None
    assert "agent-skill" in result
