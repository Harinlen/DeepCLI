# Kernel Completed Plans Index

这个目录只保留 completed plans 的总索引。归档计划本体按当前 owner
放在对应 subsystem / interface / architecture 旁边；仍然指导下一步工作的计划
留在 `docs/plans/`。

## Architecture / Control Plane

| 文档 | 归档原因 |
|---|---|
| [`agent-control-plane.md`](../../architecture/history/agent-control-plane.md) | Single-Primary Agent Control Plane 主路径已落地；后续 peer durable agents 属于 roadmap/backlog。 |
| [`agent-control-plane-b0-baseline.md`](../../architecture/history/agent-control-plane-b0-baseline.md) | Agent Control Plane B0 baseline 已落地，主线状态见 `agent-control-plane.md`。 |
| [`agent-control-plane-notes.md`](../../architecture/history/agent-control-plane-notes.md) | Agent Control Plane 详细讨论记录，作为历史参考保留。 |
| [`supervisor-controlled-restart-plan.md`](../../architecture/history/supervisor-controlled-restart-plan.md) | Supervisor-owned runtime lifecycle 和 restart/status/self-protection 主路径已实现。 |

## Interfaces

| 文档 | 归档原因 |
|---|---|
| [`acp-acpx-schema-alignment-plan.md`](../../interfaces/protocol/history/acp-acpx-schema-alignment-plan.md) | ACP namespace/schema alignment 已落地；当前协议事实见 `docs/kernel/interfaces/protocol.md`。 |

## Subsystems

| Owner | 文档 | 归档原因 |
|---|---|---|
| Orchestrator | [`orchestrator-module-refactor-plan.md`](../../subsystems/orchestrator/history/orchestrator-module-refactor-plan.md) | Orchestrator 模块拆分已实现。 |
| Prompts | [`prompt-alignment-with-cc.md`](../../subsystems/prompts/history/prompt-alignment-with-cc.md) | 主要 prompt alignment 阶段已落地，剩余 defer 项进入 backlog/后续计划。 |
| Session | [`session-acp-compliance-refactor.md`](../../subsystems/session/history/session-acp-compliance-refactor.md) | Session ACP 兼容性重构已实现。 |
| Session | [`session-lifecycle-actions.md`](../../subsystems/session/history/session-lifecycle-actions.md) | Session delete/rename/archive lifecycle actions 已实现。 |
| Session | [`session-module-refactor-plan.md`](../../subsystems/session/history/session-module-refactor-plan.md) | Session 模块拆分已实现。 |

## Testing

| 文档 | 归档原因 |
|---|---|
| [`kernel-unit-test-phase1.md`](../../testing/history/kernel-unit-test-phase1.md) | Kernel Phase 1 owned coverage goals 已完成，残余覆盖缺口已转为后续测试策略。 |
