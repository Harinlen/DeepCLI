# Kernel Completed Plans Archive

这个目录保存已经落地、或已经转为历史参考的 Kernel 计划。

仍然指导下一步工作的计划留在 `docs/plans/`；这里的文档主要用于追溯实现背景、
批次边界和当时的验收标准。

| 文档 | 归档原因 |
|---|---|
| `acp-acpx-schema-alignment-plan.md` | ACP namespace/schema alignment 已落地；当前协议事实见 `docs/kernel/interfaces/protocol.md`。 |
| `agent-control-plane.md` | Single-Primary Agent Control Plane 主路径已落地；后续 peer durable agents 属于 roadmap/backlog。 |
| `agent-control-plane-b0-baseline.md` | Agent Control Plane B0 baseline 已落地，主线状态见 `agent-control-plane.md`。 |
| `agent-control-plane-notes.md` | Agent Control Plane 详细讨论记录，作为历史参考保留。 |
| `kernel-unit-test-phase1.md` | Kernel Phase 1 owned coverage goals 已完成，残余覆盖缺口已转为后续测试策略。 |
| `orchestrator-module-refactor-plan.md` | Orchestrator 模块拆分已实现。 |
| `prompt-alignment-with-cc.md` | 主要 prompt alignment 阶段已落地，剩余 defer 项进入 backlog/后续计划。 |
| `session-acp-compliance-refactor.md` | Session ACP 兼容性重构已实现。 |
| `session-lifecycle-actions.md` | Session delete/rename/archive lifecycle actions 已实现。 |
| `session-module-refactor-plan.md` | Session 模块拆分已实现。 |
| `supervisor-controlled-restart-plan.md` | Supervisor-owned runtime lifecycle 和 restart/status/self-protection 主路径已实现。 |
