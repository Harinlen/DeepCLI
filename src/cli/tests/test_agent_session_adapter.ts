import { MustangAgentSessionAdapter } from "../src/session/agent-session-adapter.js";
import { assert } from "./helpers.js";

const updates = [
	{ sessionUpdate: "agent_thought_chunk", content: { type: "text", text: "thinking" } },
	{ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "hello" } },
	{ sessionUpdate: "tool_call", toolCallId: "tool-1", title: "Bash", rawInput: "{\"command\":\"pwd\"}" },
	{ sessionUpdate: "tool_call_update", toolCallId: "tool-1", status: "in_progress", content: "running" },
	{ sessionUpdate: "tool_call_update", toolCallId: "tool-1", status: "completed", content: "done" },
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
	modelProfiles: [],
});

const events: string[] = [];
adapter.subscribe(event => {
	events.push(event.type);
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

const assistant = adapter.messages.find(message => message.role === "assistant");
assert(assistant?.content.some((block: { type: string; text?: string }) => block.type === "text" && block.text === "hello"), "assistant text chunk should be appended");
assert(assistant?.content.some((block: { type: string; thinking?: string }) => block.type === "thinking" && block.thinking === "thinking"), "assistant thinking chunk should be appended");
assert(assistant?.content.some((block: { type: string; id?: string }) => block.type === "toolCall" && block.id === "tool-1"), "tool call should be appended to assistant message");

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

console.log("PASS: agent session adapter");
