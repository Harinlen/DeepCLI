"""Bundled /loop skill — create recurring cron jobs from natural language.

Usage: ``/loop 5m /check-build`` or ``/loop check status every 30m``

Parses the user's interval and prompt, then instructs the LLM to call
CronCreate with the appropriate schedule expression.
"""

from __future__ import annotations

from kernel.skills.bundled import BundledSkillDef, register_bundled_skill

_LOOP_BODY = """\
You are executing the /loop command. The user wants to create a recurring
cron job.

Parse the user's input to extract:
1. **Interval** — a duration like "5m", "2h", "30s", "1d" (or "every 5m" etc.)
2. **Prompt** — the task to execute at each interval

Then call CronCreate (load it via ToolSearch first) with:
- schedule: "every <interval>" (e.g. "every 5m")
- prompt: the extracted prompt text
- recurring: true
- description: a brief description of what the job does

If no interval is specified, create a one-shot job with schedule "5m"
(delay) and include in the prompt instructions for the agent to create
another one-shot CronCreate at the end if it wants to continue
(dynamic self-scheduling mode).

After creating the job, report the job ID and next fire time.

User input: $ARGUMENTS
"""

_loop_skill = register_bundled_skill(
    BundledSkillDef(
        name="loop",
        description="Create a recurring cron job from natural language",
        when_to_use=(
            "When the user says /loop, wants to repeat a task on an interval, "
            "or asks to 'keep checking' / 'monitor periodically'."
        ),
        allowed_tools=("ToolSearch", "CronCreate"),
        argument_hint="[interval] <prompt>",
        user_invocable=True,
        body=_LOOP_BODY,
    )
)
