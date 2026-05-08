# Kernel History

This is the durable milestone map for shipped kernel work.  It records what
landed and where to read the current design.  Keep low-level behavior in the
subsystem docs rather than expanding this into a second progress log.

## Foundation

| Milestone | What Landed | Current Docs |
|---|---|---|
| Kernel bootstrap | FastAPI lifespan, module table, bootstrap services, subsystem load/unload contract. | [architecture.md](architecture.md) |
| Config / flags / secrets | Layered config, runtime-frozen flags, SQLite secret store, config secret expansion. | [config.md](subsystems/config.md), [flags.md](subsystems/flags.md), [secrets.md](subsystems/secrets.md) |
| Connection AuthN | WebSocket accept-time identity with token/password credentials. | [connection_authenticator.md](subsystems/connection_authenticator.md) |
| LLM stack | Provider lifecycle, model/profile config, alias resolution, role-based `current_used`, streaming delegation. | [llm_provider.md](subsystems/llm_provider.md), [llm.md](subsystems/llm.md) |
| Transport / protocol | WebSocket `/session`, ACP protocol stack, JSON-RPC/Pydantic routing. | [transport.md](subsystems/transport.md), [interfaces/protocol.md](interfaces/protocol.md) |

## Session And Orchestration

| Milestone | What Landed | Current Docs |
|---|---|---|
| SessionManager | SQLite session store, event persistence, FIFO prompt turns, cancellation, multi-connection broadcast. | [session.md](subsystems/session.md) |
| Session ACP compliance | ACP `SessionInfo` shape, `_meta`, `updatedAt`, config options, modes, archive/rename/delete lifecycle actions. | [session.md](subsystems/session.md), [history/plans/session-acp-compliance-refactor.md](history/plans/session-acp-compliance-refactor.md) |
| Orchestrator | LLM/tool loop, history, prompt assembly, compaction, plan mode, cancellation hygiene. | [orchestrator.md](subsystems/orchestrator.md), [compaction.md](subsystems/compaction.md) |
| Prompt alignment | PromptManager, file-backed prompt templates, CC-aligned tool descriptions, language section, MCP instructions. | [prompts.md](subsystems/prompts.md), [history/plans/prompt-alignment-with-cc.md](history/plans/prompt-alignment-with-cc.md) |

## Tools, Authz, And Extensibility

| Milestone | What Landed | Current Docs |
|---|---|---|
| ToolManager + ToolAuthorizer | Tool ABC, ToolContext, registry layers, file-state checks, permission rules, session grants, permission prompts. | [tools.md](subsystems/tools.md), [tool_authorizer.md](subsystems/tool_authorizer.md) |
| BashClassifier | Compound read-only command classification, destructive warnings, LLMJudge path. | [tool_authorizer.md](subsystems/tool_authorizer.md) |
| Hooks | Event manifest discovery, `HookEventCtx`, blockable tool hooks, permission hooks, cron hooks, reminder drain. | [hooks.md](subsystems/hooks.md) |
| MCPManager | stdio/SSE/HTTP/WebSocket clients, health/reconnect, MCP tool adapter, OAuth helper tool. | [mcp.md](subsystems/mcp.md) |
| SkillManager | SKILL.md discovery, lazy loading, conditional/dynamic pools, SkillTool, bundled skills. | [skills.md](subsystems/skills.md) |
| MemoryManager | Global/project memory, cognitive categories, BM25+LLM scoring, memory tools, background extraction. | [memory/design.md](subsystems/memory/design.md) |
| Task / Agent tools | TaskRegistry, AgentTool, TodoWrite, TaskOutput, TaskStop, sub-agent transcript capture. | [tasks.md](subsystems/tasks.md) |
| SendMessage | In-session, transcript resume, and cross-session ACP message routing. | [tools.md](subsystems/tools.md), [tasks.md](subsystems/tasks.md) |
| ScheduleManager | Cron store/scheduler/executor, delivery router, cron tools, `/loop` skill. | [schedule.md](subsystems/schedule.md) |
| GitManager | Git context injection, dynamic worktree tools, worktree store, session cwd resume. | [git.md](subsystems/git.md) |
| Command / gateways | Slash command catalog and Discord gateway adapter. | [commands.md](subsystems/commands.md), [gateways.md](subsystems/gateways.md) |

## Protocol And Control Plane

| Milestone | What Landed | Current Docs |
|---|---|---|
| ACP namespace migration | DeepCLI extensions moved to `_mustang.agent/*`; temporary legacy aliases removed after CLI/probe migration. | [interfaces/protocol.md](interfaces/protocol.md), [history/plans/acp-acpx-schema-alignment-plan.md](history/plans/acp-acpx-schema-alignment-plan.md) |
| ACP `_meta` migration | Request `_meta` reaches schema models; session filters/worktree/archive/title metadata use `mustang.agent/*`; REPL execution updates are namespaced. | [interfaces/protocol.md](interfaces/protocol.md) |
| Agent Control Plane Batch A | Shared runtime kinds, statuses, queue states, control operations, identity/status/result dataclasses, and `AgentRuntimeController`. No runtime control dispatch yet. | [history/plans/agent-control-plane.md](history/plans/agent-control-plane.md) |

## Refactors And Quality Work

| Milestone | What Landed | Current Docs |
|---|---|---|
| Session module split | `SessionManager` moved out of monolithic `session/__init__.py`; session internals grouped by API/lifecycle/turns/client-stream/orchestration/persistence/runtime. | [session.md](subsystems/session.md), [history/plans/session-module-refactor-plan.md](history/plans/session-module-refactor-plan.md) |
| Kernel file-length plan | Repo-wide Python file-length audit and batch plan. | [../plans/kernel-file-length-refactor-plan.md](../plans/kernel-file-length-refactor-plan.md) |

## Notes For Future Updates

- Prefer one row per durable capability, not one row per bug fix.
- If a subsystem doc is missing or stale, update that doc before adding a long
  explanation here.
- Keep verification details in the plan or pull request notes unless they are
  needed to explain a persistent contract.
