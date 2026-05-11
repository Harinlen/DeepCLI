import { MustangAgentSessionAdapter } from "../src/session/agent-session-adapter.js";
import { getRecentSessions } from "../src/active-port/coding-agent/session/session-manager.js";
import { assert } from "./helpers.js";

const updates = [
	{ sessionUpdate: "agent_thought_chunk", content: { type: "text", text: "thinking" } },
	{ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "hello" } },
	{
		sessionUpdate: "tool_call",
		toolCallId: "tool-1",
		title: "Bash",
		rawInput: "{\"command\":\"pwd\"}",
		_meta: { "mustang.agent/toolBackend": { backend: "shell", kind: "command", phase: "pending" } },
	},
	{ sessionUpdate: "tool_call_update", toolCallId: "tool-1", status: "in_progress", content: "running" },
	{
		sessionUpdate: "tool_call_update",
		toolCallId: "tool-1",
		status: "completed",
		content: "done",
		_meta: { "mustang.agent/toolBackend": { backend: "shell", kind: "command" } },
	},
	{ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "after" } },
	{ sessionUpdate: "usage_update", inputTokens: 123, outputTokens: 45, used: 234, size: 64_000, durationMs: 1500 },
	{ sessionUpdate: "session_info_update", title: "New title" },
	{ sessionUpdate: "current_mode_update", sessionId: "sess-1", modeId: "plan" },
];

const fakeSession = {
	sessionId: "sess-1",
	summary: {
		sessionId: "sess-1",
		title: "Old title",
		cwd: "/tmp",
		titleSource: "auto",
	},
	async prompt(_text: string, onUpdate: (update: unknown) => void) {
		for (const update of updates) onUpdate(update);
		return { stopReason: "stop" };
	},
	setMode: async (_mode: string) => {},
	getUsage: async () => ({
		sessionId: "sess-1",
		cwd: "/tmp",
		kernelVersion: "1.0.0",
		tokens: { input: 123, output: 45, cacheRead: 0, cacheWrite: 0, total: 168 },
		context: { totalTokens: 168, contextWindow: 64_000, percent: 0.3, sections: [] },
		history: { messages: 2, turns: 1, toolCalls: 1, compactions: 0, queuedTurns: 0, inFlight: false },
		memory: { loaded: 0, writableScopes: 0 },
		environment: { lspServers: [], mcpServers: [] },
	}),
	cancel() {},
	cancelExecution() {},
};

const fakeSessionService = {
	async rename(_sessionId: string, title: string) {
		return { ...fakeSession.summary, title, titleSource: "user" };
	},
	create: async () => ({ sessionId: "new-session", modes: { currentModeId: "default" } }),
	list: async () => [
		{
			sessionId: "old-session",
			path: "old-session",
			title: "Old listed session",
			cwd: "/tmp",
			updatedAt: "2026-05-08T00:00:00.000Z",
			createdAt: "2026-05-08T00:00:00.000Z",
			archivedAt: null,
			titleSource: "auto",
			totalInputTokens: null,
			totalOutputTokens: null,
			messageCount: 1,
			turnCount: 1,
			raw: { sessionId: "old-session" },
		},
	],
	clientForSession: () => ({}),
};

const adapter = new MustangAgentSessionAdapter({
	client: {} as never,
	session: fakeSession as never,
	sessionService: fakeSessionService as never,
	modelProfiles: [{ name: "deepseek/deepseek-chat", providerName: "deepseek", providerType: "deepseek", modelId: "deepseek-chat", isDefault: true, contextWindow: 64_000 }],
});

const events: string[] = [];
const renderOrder: string[] = [];
let toolBackendDetails: Record<string, unknown> | undefined;
let pendingToolBackendDetails: Record<string, unknown> | undefined;
adapter.subscribe(event => {
	events.push(event.type);
	const message = event.message as { role?: string; content?: Array<{ type: string; text?: string; thinking?: string }> } | undefined;
	if (event.type === "message_start" && message?.role === "assistant") {
		renderOrder.push("assistant:start");
	}
	if (event.type === "message_end" && message?.role === "assistant") {
		const text = (message.content ?? [])
			.filter((block: { type: string }) => block.type === "text" || block.type === "thinking")
			.map((block: { text?: string; thinking?: string }) => block.text ?? block.thinking ?? "")
			.join("");
		renderOrder.push(`assistant:end:${text}`);
	}
	if (event.type === "tool_execution_start") {
		renderOrder.push(`tool:${event.toolCallId}`);
		if (event.toolCallId === "tool-1") {
			const details = event.details as { backend?: Record<string, unknown> } | undefined;
			pendingToolBackendDetails = details?.backend;
		}
	}
	if (event.type === "tool_execution_end" && event.toolCallId === "tool-1") {
		const result = event.result as { details?: Record<string, unknown> } | undefined;
		toolBackendDetails = result?.details?.backend as Record<string, unknown> | undefined;
	}
});

await adapter.prompt("hi");

assert(events.includes("agent_start"), "adapter should emit agent_start");
assert(events.includes("message_update"), "adapter should emit streaming message_update");
assert(events.includes("tool_execution_start"), "adapter should emit tool start");
assert(events.includes("tool_execution_update"), "adapter should emit tool progress");
assert(events.includes("tool_execution_end"), "adapter should emit tool completion");
assert(pendingToolBackendDetails?.backend === "shell", "adapter should expose pending tool backend metadata");
assert(toolBackendDetails?.backend === "shell", "adapter should expose tool backend metadata");
assert(events.includes("agent_end"), "adapter should emit agent_end");
assert(events.includes("current_mode_update"), "adapter should emit current_mode_update for UI mode reconciliation");
assert(adapter.messages.length === 2, "adapter should retain user and assistant messages");
assert(adapter.sessionManager.getSessionName() === "New title", "session_info_update should refresh local title");
assert(adapter.isStreaming === false, "adapter should clear streaming flag after prompt");
const stats = adapter.getSessionStats();
assert(stats.sessionId === "sess-1", "/session stats should include session id");
assert(stats.userMessages === 1, "/session stats should count user messages");
assert(stats.assistantMessages === 1, "/session stats should count assistant messages");
assert(stats.toolCalls === 1, "/session stats should count tool calls");
assert(stats.tokens.input === 123, "/session stats should include live input token totals");
assert(stats.tokens.output === 45, "/session stats should include live output token totals");
assert(stats.tokens.total === 168, "/session stats should expose token totals");
const usageStats = adapter.sessionManager.getUsageStatistics();
assert(usageStats.input === 123, "status line usage stats should include live input tokens");
assert(usageStats.output === 45, "status line usage stats should include live output tokens");
assert(adapter.sessionManager.getContextUsage().totalTokens === 234, "status line context should use kernel usage_update snapshot");
assert(adapter.sessionManager.getContextUsage().contextWindow === 64_000, "status line context should use kernel context window snapshot");
assert(adapter.state.model.contextWindow === 64_000, "adapter should expose model context window to status line");
assert(adapter.getAsyncJobSnapshot().running.length === 0, "adapter should expose empty async job snapshot");
assert(adapter.modelRegistry.authStorage.hasOAuth("deepseek") === false, "adapter should expose no-op OAuth auth storage");
assert(adapter.modelRegistry.authStorage.has("deepseek") === false, "adapter should expose no-op API key auth storage");
assert(adapter.modelRegistry.authStorage.hasAuth("deepseek") === false, "adapter should expose no-op fallback auth storage");
assert(adapter.currentPermissionMode === "plan", "adapter should track kernel current mode updates");
const costReport = await adapter.fetchCostReport();
assert(costReport.tokens.total === 168, "adapter should fetch /cost usage from the active session");
assert(
	renderOrder.join("|") === "assistant:start|assistant:end:thinkinghello|tool:tool-1|assistant:start|assistant:end:after",
	`adapter should project ACP stream into ordered render blocks, got: ${renderOrder.join("|")}`,
);

const assistant = adapter.messages.find(message => message.role === "assistant");
assert(assistant?.content.some((block: { type: string; text?: string }) => block.type === "text" && block.text === "hello"), "assistant text chunk should be appended");
assert(assistant?.content.some((block: { type: string; text?: string }) => block.type === "text" && block.text === "after"), "assistant text after tool should be appended");
assert(assistant?.content.some((block: { type: string; thinking?: string }) => block.type === "thinking" && block.thinking === "thinking"), "assistant thinking chunk should be appended");
assert(assistant?.content.some((block: { type: string; id?: string }) => block.type === "toolCall" && block.id === "tool-1"), "tool call should be appended to assistant message");
assert(assistant?.usage?.input === 123 && assistant?.usage?.output === 45, "usage_update should attach usage to assistant message");
assert(assistant?.duration === 1500, "usage_update should attach response duration to assistant message");

await adapter.setPermissionMode("default");
await adapter.createSession();
assert(adapter.sessionId === "new-session", "createSession should switch to the new kernel session");
assert(adapter.messages.length === 0, "createSession should clear transcript state from the previous session");
assert(adapter.sessionManager.getContextUsage().totalTokens === 0, "createSession should reset context to the new session snapshot");
const recentAfterCreate = await getRecentSessions("/tmp");
assert(recentAfterCreate[0]?.id === "new-session", "welcome recents should include the active newly-created session");
assert(recentAfterCreate[0]?.title === "Untitled session", "welcome recents should label empty active sessions");
assert(recentAfterCreate.some(session => session.id === "old-session"), "welcome recents should still include kernel-listed sessions");

const emptyUsageCalls: unknown[] = [];
const emptyUsageAdapter = new MustangAgentSessionAdapter({
	client: {
		request: async (_method: string, params: unknown) => {
			emptyUsageCalls.push(params);
			return {
				sessionId: "",
				cwd: "/tmp",
				kernelVersion: "1.0.0",
				tokens: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
				context: { totalTokens: 0, contextWindow: null, percent: 0, sections: [] },
				history: { messages: 0, turns: 0, toolCalls: 0, compactions: 0, queuedTurns: 0, inFlight: false },
				memory: { loaded: 0, writableScopes: 0 },
				environment: { lspServers: [], mcpServers: [] },
			};
		},
	} as never,
	sessionService: fakeSessionService as never,
	modelProfiles: [{ name: "deepseek/deepseek-chat", providerName: "deepseek", providerType: "deepseek", modelId: "deepseek-chat", isDefault: true, contextWindow: 64_000 }],
});
const emptyCostReport = await emptyUsageAdapter.fetchCostReport();
assert(emptyCostReport.tokens.total === 0, "adapter should fetch empty /cost usage before a session exists");
assert(JSON.stringify(emptyUsageCalls[0]) === "{}", "empty /cost should call the kernel without creating or requiring a session");

const subagentUpdates = [
	{ sessionUpdate: "tool_call", toolCallId: "agent-1", title: "Agent", rawInput: "{\"description\":\"Check weather\",\"prompt\":\"Look up weather\"}" },
	{ sessionUpdate: "tool_call_update", toolCallId: "agent-1", status: "in_progress", meta: { "mustang.agent/agentStart": { agent_id: "a1" } } },
	{ sessionUpdate: "agent_thought_chunk", content: { type: "text", text: "child thought" } },
	{ sessionUpdate: "tool_call", toolCallId: "child-web", title: "WebSearch", rawInput: "{\"query\":\"weather\"}" },
	{ sessionUpdate: "tool_call_update", toolCallId: "child-web", status: "completed", content: "child search result" },
	{ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "child final" } },
	{ sessionUpdate: "tool_call_update", toolCallId: "agent-1", status: "in_progress", meta: { "mustang.agent/agentEnd": { agent_id: "a1" } } },
	{
		sessionUpdate: "tool_call_update",
		toolCallId: "agent-1",
		status: "completed",
		content: "child final",
		_meta: {
			"mustang.agent/agentStats": {
				toolUseCount: 2,
				inputTokens: 1000,
				outputTokens: 500,
				totalTokens: 1500,
				durationMs: 13000,
			},
		},
	},
	{ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "parent summary" } },
];
const subagentSession = {
	...fakeSession,
	async prompt(_text: string, onUpdate: (update: unknown) => void) {
		for (const update of subagentUpdates) onUpdate(update);
		return { stopReason: "stop" };
	},
};
const subagentAdapter = new MustangAgentSessionAdapter({
	client: {} as never,
	session: subagentSession as never,
	sessionService: fakeSessionService as never,
	modelProfiles: [],
});
const subagentRenderOrder: string[] = [];
let subagentAgentStats: Record<string, unknown> | undefined;
subagentAdapter.subscribe(event => {
	if (event.type === "tool_execution_start" || event.type === "tool_execution_end") {
		subagentRenderOrder.push(`${event.type}:${event.toolCallId}:${event.toolName}`);
	}
	if (event.type === "tool_execution_end" && event.toolCallId === "agent-1") {
		const result = event.result as { details?: { agent?: Record<string, unknown> } } | undefined;
		subagentAgentStats = result?.details?.agent;
	}
});
await subagentAdapter.prompt("use agent");
const subagentAssistant = subagentAdapter.messages.find(message => message.role === "assistant");
const subagentText = (subagentAssistant?.content ?? [])
	.filter((block: { type: string }) => block.type === "text" || block.type === "thinking")
	.map((block: { text?: string; thinking?: string }) => block.text ?? block.thinking ?? "")
	.join("");
assert(subagentRenderOrder.join("|") === "tool_execution_start:agent-1:Agent|tool_execution_end:agent-1:Agent", `sub-agent child tools should stay inside Agent UI, got: ${subagentRenderOrder.join("|")}`);
assert(subagentAgentStats?.totalTokens === 1500, "Agent tool completion should expose sub-agent token stats");
assert(subagentAgentStats?.toolUseCount === 2, "Agent tool completion should expose sub-agent tool use count");
assert(subagentAgentStats?.durationMs === 13000, "Agent tool completion should expose sub-agent duration");
assert(subagentText === "parent summary", `sub-agent private text should not render as parent assistant text, got: ${subagentText}`);
assert(!subagentAssistant?.content.some((block: { type: string; id?: string }) => block.type === "toolCall" && block.id === "child-web"), "child tool calls should not be appended to parent assistant message");

const delayedAdapter = new MustangAgentSessionAdapter({
	client: {} as never,
	session: fakeSession as never,
	sessionService: fakeSessionService as never,
	modelProfiles: [],
});
const delayedEvents: string[] = [];
delayedAdapter.subscribe(async event => {
	if (event.type === "message_update" || event.type === "tool_execution_end") {
		await new Promise(resolve => setTimeout(resolve, 20));
	}
	delayedEvents.push(event.type);
});

await delayedAdapter.prompt("hi");

const messageUpdateIndex = delayedEvents.indexOf("message_update");
const messageEndIndex = delayedEvents.lastIndexOf("message_end");
const agentEndIndex = delayedEvents.indexOf("agent_end");
assert(messageUpdateIndex !== -1, "delayed listener should still receive message_update before prompt returns");
assert(agentEndIndex !== -1, "delayed listener should receive agent_end before prompt returns");
assert(
	messageUpdateIndex < messageEndIndex && messageEndIndex < agentEndIndex,
	`session events should be flushed in order, got: ${delayedEvents.join(",")}`,
);

let lazyCreateCalls = 0;
const lazyClient = {
	request: async (_method: string, _params: unknown) => ({}),
	notify: () => {},
	promptRequest: async (_sessionId: string, _text: string) => ({ stopReason: "stop" }),
	executeShellRequest: async () => ({ exitCode: 0, cancelled: false }),
	executePythonRequest: async () => ({ exitCode: 0, cancelled: false }),
	onUpdate: () => () => {},
};
const lazyAdapter = new MustangAgentSessionAdapter({
	client: lazyClient as never,
	sessionService: {
		create: async () => {
			lazyCreateCalls += 1;
			return { sessionId: "lazy-session" };
		},
		clientForSession: () => lazyClient,
		list: async () => [],
	} as never,
});
assert(lazyAdapter.sessionId === "pending", "adapter without startup session should expose pending session id");
try {
	await lazyAdapter.executeBash("pwd", () => {});
	assert(false, "shell execution should not create a lazy chat session");
} catch (error) {
	assert((error as Error).message.includes("Run a chat prompt"), "shell execution should fail without creating a session");
}
assert(lazyCreateCalls === 0, "commands should not create lazy chat sessions");
await lazyAdapter.cyclePermissionMode();
assert(lazyAdapter.currentPermissionMode === "accept_edits", "Shift+Tab cycle should advance pending mode before session creation");
await lazyAdapter.prompt("hello");
assert(lazyCreateCalls === 1, "first chat prompt should create the lazy session");
assert(lazyAdapter.sessionId === "lazy-session", "lazy session id should update after first prompt");

let loadUpdateHandler: ((update: any) => void) | undefined;
const replayClient = {
	request: async () => ({}),
	notify: () => {},
	promptRequest: async () => ({ stopReason: "stop" }),
	executeShellRequest: async () => ({ exitCode: 0, cancelled: false }),
	executePythonRequest: async () => ({ exitCode: 0, cancelled: false }),
	onUpdate: (handler: (update: any) => void) => {
		loadUpdateHandler = handler;
		return () => {
			if (loadUpdateHandler === handler) loadUpdateHandler = undefined;
		};
	},
};
const replayAdapter = new MustangAgentSessionAdapter({
	client: replayClient as never,
	sessionService: {
		clientForSession: () => replayClient,
		load: async () => {
				loadUpdateHandler?.({ sessionUpdate: "user_message_chunk", content: { type: "text", text: "old question" } });
				setTimeout(() => {
					loadUpdateHandler?.({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "late answer" } });
					loadUpdateHandler?.({ sessionUpdate: "usage_update", inputTokens: 78_768, outputTokens: 1_080, used: 79_848, size: 1_000_000, durationMs: 147_622 });
				}, 10);
			loadUpdateHandler?.({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "old answer" } });
			loadUpdateHandler?.({ sessionUpdate: "tool_call", toolCallId: "old-tool", title: "Bash", rawInput: "{\"command\":\"pwd\"}" });
			loadUpdateHandler?.({ sessionUpdate: "tool_call_update", toolCallId: "old-tool", status: "completed", content: "done" });
			return { session: { sessionId: "loaded-session", title: "Loaded", cwd: "/tmp" } };
		},
		list: async () => [],
		create: async () => ({ sessionId: "unused" }),
	} as never,
});
await replayAdapter.loadSession("loaded-session");
assert(replayAdapter.sessionId === "loaded-session", "session/load should keep the requested session id when the kernel omits top-level sessionId");
assert(replayAdapter.messages.some((message: any) => message.role === "user" && message.content?.[0]?.text === "old question"), "session/load replay should rebuild user messages");
const replayAssistant = replayAdapter.messages.find((message: any) => message.role === "assistant");
assert(replayAssistant?.content.some((block: any) => block.type === "text" && block.text === "old answer"), "session/load replay should rebuild assistant text");
assert(replayAssistant?.content.some((block: any) => block.type === "text" && block.text === "late answer"), "session/load replay should keep listening for updates that arrive after the response");
assert(replayAssistant?.content.some((block: any) => block.type === "toolCall" && block.id === "old-tool"), "session/load replay should rebuild tool calls");
assert(replayAssistant?.usage?.input === 78_768 && replayAssistant?.usage?.output === 1_080, "late usage_update should attach to resumed transcript");
assert(replayAdapter.sessionManager.getContextUsage().totalTokens === 79_848, "session/load should preserve replayed kernel context snapshot after switching session");
assert(replayAdapter.sessionManager.getContextUsage().contextWindow === 1_000_000, "session/load should preserve replayed kernel context window after switching session");

const modeCalls: Array<{ method: string; params: { modeId?: string; sessionId?: string } }> = [];
const modeClient = {
	request: async (method: string, params: { modeId?: string; sessionId?: string }) => {
		modeCalls.push({ method, params });
		return {};
	},
	notify: () => {},
	promptRequest: async (_sessionId: string, _text: string) => ({ stopReason: "stop" }),
	executeShellRequest: async () => ({ exitCode: 0, cancelled: false }),
	executePythonRequest: async () => ({ exitCode: 0, cancelled: false }),
	onUpdate: (handler: (update: unknown) => void) => {
		handler({ sessionUpdate: "current_mode_update", sessionId: "mode-session", modeId: "auto" });
		return () => {};
	},
};
const modeAdapter = new MustangAgentSessionAdapter({
	client: modeClient as never,
	session: new (class extends Object {
		sessionId = "mode-session";
		setMode(mode: string) {
			return modeClient.request("session/set_mode", { sessionId: this.sessionId, modeId: mode });
		}
		cancel() {}
		cancelExecution() {}
	})() as never,
	sessionService: fakeSessionService as never,
});
assert(modeAdapter.currentPermissionMode === "auto", "ambient current_mode_update should refresh adapter mode");
await modeAdapter.cyclePermissionMode();
assert(modeAdapter.currentPermissionMode === "bypass", "cycle should follow Auto -> Bypass");
assert(modeCalls.some(call => call.params.modeId === "bypass"), "cycle should call session/set_mode with next mode");

const promptModeOrder: string[] = [];
const promptModeAdapter = new MustangAgentSessionAdapter({
	client: { onUpdate: () => () => {} } as never,
	session: {
		sessionId: "prompt-mode-session",
		summary: { sessionId: "prompt-mode-session", modes: { currentModeId: "bypass" } },
		setMode: async (mode: string) => {
			promptModeOrder.push(`set:${mode}`);
		},
		prompt: async (_text: string, _onUpdate: unknown, options?: { mode?: string }) => {
			promptModeOrder.push(`prompt:${options?.mode ?? "none"}`);
			return { stopReason: "stop" };
		},
		cancel() {},
		cancelExecution() {},
	} as never,
	sessionService: fakeSessionService as never,
});
assert(promptModeAdapter.currentPermissionMode === "bypass", "adapter should restore startup mode from session summary");
await promptModeAdapter.prompt("sync before prompt");
assert(
	promptModeOrder.join("|") === "prompt:bypass",
	`adapter should pass current permission mode into prompt, got: ${promptModeOrder.join("|")}`,
);

console.log("PASS: agent session adapter");
