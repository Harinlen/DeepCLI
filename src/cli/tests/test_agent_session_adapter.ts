import { MustangAgentSessionAdapter } from "../src/session/agent-session-adapter.js";
import { assert } from "./helpers.js";

const updates = [
	{ sessionUpdate: "agent_thought_chunk", content: { type: "text", text: "thinking" } },
	{ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "hello" } },
	{ sessionUpdate: "tool_call", toolCallId: "tool-1", title: "Bash", rawInput: "{\"command\":\"pwd\"}" },
	{ sessionUpdate: "tool_call_update", toolCallId: "tool-1", status: "in_progress", content: "running" },
	{ sessionUpdate: "tool_call_update", toolCallId: "tool-1", status: "completed", content: "done" },
	{ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "after" } },
	{ sessionUpdate: "usage_update", inputTokens: 123, outputTokens: 45, durationMs: 1500 },
	{ sessionUpdate: "session_info_update", title: "New title" },
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
	cancel() {},
	cancelExecution() {},
};

const fakeSessionService = {
	async rename(_sessionId: string, title: string) {
		return { ...fakeSession.summary, title, titleSource: "user" };
	},
	create: async () => ({ sessionId: "new-session" }),
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
	}
});

await adapter.prompt("hi");

assert(events.includes("agent_start"), "adapter should emit agent_start");
assert(events.includes("message_update"), "adapter should emit streaming message_update");
assert(events.includes("tool_execution_start"), "adapter should emit tool start");
assert(events.includes("tool_execution_update"), "adapter should emit tool progress");
assert(events.includes("tool_execution_end"), "adapter should emit tool completion");
assert(events.includes("agent_end"), "adapter should emit agent_end");
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
assert(adapter.state.model.contextWindow === 64_000, "adapter should expose model context window to status line");
assert(adapter.getAsyncJobSnapshot().running.length === 0, "adapter should expose empty async job snapshot");
assert(adapter.modelRegistry.authStorage.hasOAuth("deepseek") === false, "adapter should expose no-op OAuth auth storage");
assert(adapter.modelRegistry.authStorage.has("deepseek") === false, "adapter should expose no-op API key auth storage");
assert(adapter.modelRegistry.authStorage.hasAuth("deepseek") === false, "adapter should expose no-op fallback auth storage");
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
await lazyAdapter.prompt("hello");
assert(lazyCreateCalls === 1, "first chat prompt should create the lazy session");
assert(lazyAdapter.sessionId === "lazy-session", "lazy session id should update after first prompt");

console.log("PASS: agent session adapter");
