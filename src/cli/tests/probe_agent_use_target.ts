import { WebSocketServer } from "ws";
import { AcpClient } from "../src/acp/client.js";
import { MustangMethod } from "../src/acp/methods.js";
import { executeBuiltinSlashCommand } from "../src/active-port/coding-agent/slash-commands/builtin-registry.js";
import { initTheme } from "../src/active-port/coding-agent/modes/theme/theme.js";
import { MustangSession } from "../src/session.js";
import { assert } from "./helpers.js";

await initTheme(false, "unicode", false, "dark", "dark");

type RpcRequest = {
	id?: number;
	method?: string;
	params?: Record<string, unknown>;
};

const server = new WebSocketServer({ port: 0 });
if (server.address() === null) {
	await new Promise<void>((resolve) => server.once("listening", resolve));
}
const port = (server.address() as { port: number }).port;
const promptParams: Record<string, unknown>[] = [];
const activateSkillParams: Record<string, unknown>[] = [];
const resumeParams: Record<string, unknown>[] = [];
const cancelParams: Record<string, unknown>[] = [];
const startParams: Record<string, unknown>[] = [];
let healthRequests = 0;

function reply(socket: { send: (data: string) => void }, id: number | undefined, result: unknown): void {
	if (id === undefined) return;
	socket.send(JSON.stringify({ jsonrpc: "2.0", id, result }));
}

async function waitFor(predicate: () => boolean, message: string): Promise<void> {
	const deadline = Date.now() + 1_000;
	while (Date.now() < deadline) {
		if (predicate()) return;
		await new Promise(resolve => setTimeout(resolve, 10));
	}
	assert(false, message);
}

server.on("connection", (socket) => {
	socket.on("message", (raw) => {
		const msg = JSON.parse(raw.toString()) as RpcRequest;
		switch (msg.method) {
			case "initialize":
				reply(socket, msg.id, {});
				return;
			case "session/resume":
				resumeParams.push(msg.params ?? {});
				reply(socket, msg.id, { modes: { currentModeId: "default" } });
				return;
			case "session/new":
				reply(socket, msg.id, { sessionId: "research-session" });
				return;
			case "session/prompt":
				promptParams.push(msg.params ?? {});
				reply(socket, msg.id, { stopReason: "end_turn" });
				return;
			case "session/cancel":
				cancelParams.push(msg.params ?? {});
				return;
			case MustangMethod.sessionActivateSkill:
				activateSkillParams.push(msg.params ?? {});
				reply(socket, msg.id, { stopReason: "end_turn" });
				return;
			case MustangMethod.agentsList:
				reply(socket, msg.id, {
					agents: [
						{ agentId: "primary", name: "Primary", status: "active", revision: 1 },
						{ agentId: "research", name: "Research", status: "active", revision: 3 },
					],
				});
				return;
			case MustangMethod.agentsHealth:
				healthRequests += 1;
				reply(socket, msg.id, {
					health: {
						agentId: msg.params?.agentId,
						routeStatus: healthRequests === 1 ? "unavailable" : "registered",
					},
				});
				return;
			case MustangMethod.agentsStart:
				startParams.push(msg.params ?? {});
				reply(socket, msg.id, { status: { agentId: msg.params?.agentId, routeStatus: "registered" } });
				return;
			default:
				reply(socket, msg.id, { ok: true, method: msg.method, params: msg.params });
		}
	});
});

const client = await AcpClient.connect(`ws://127.0.0.1:${port}`, "dev");
const session = new MustangSession(client, "sess-1");
const rendered: string[] = [];
const warnings: string[] = [];
const errors: string[] = [];
const statusInvalidations: string[] = [];
let topBorderUpdates = 0;
const ctx = {
	session,
	chatContainer: {
		addChild(node: unknown) {
			const text = typeof (node as { getText?: () => string }).getText === "function"
				? (node as { getText: () => string }).getText()
				: String(node);
			rendered.push(text);
		},
	},
	ui: { requestRender: () => undefined },
	statusLine: { invalidate: () => statusInvalidations.push("invalidate") },
	updateEditorTopBorder: () => { topBorderUpdates += 1; },
	showWarning: (message: string) => warnings.push(message),
	showError: (message: string) => errors.push(message),
};

await executeBuiltinSlashCommand("/agent use research", { ctx });
assert(errors.length === 0, `/agent use research should not error: ${errors.join(" | ")}`);
assert(warnings.length === 0, `/agent use research should not warn: ${warnings.join(" | ")}`);
assert(session.targetAgentId === "research", "/agent use research should store targetAgentId=research");
assert(session.targetAgentSessionId === "research-session", "/agent use research should create a target session");
assert(startParams[0]?.agentId === "research", "/agent use research should auto-start an unavailable target route");
assert(statusInvalidations.length === 1, "/agent use research should invalidate status line once");
assert(topBorderUpdates === 1, "/agent use research should update the editor status border");

await session.prompt("hello research", () => undefined);
assert(resumeParams[0]?.sessionId === "research-session", "target prompt should resume the target agent session");
assert(resumeParams[0]?.agentId === "research", "target prompt resume should include agentId=research");
assert(promptParams[0]?.sessionId === "research-session", "target prompt should use the target agent session id");
assert(promptParams[0]?.agentId === "research", "ordinary prompt should include agentId=research after /agent use");
session.cancel();
await waitFor(() => cancelParams.length >= 1, "target cancel notification should be observed");
assert(cancelParams[0]?.sessionId === "research-session", "target cancel should use the target agent session id");
assert(cancelParams[0]?.agentId === "research", "target cancel should include agentId=research after /agent use");

await session.activateSkill("reviewer", "check target", () => undefined);
assert(activateSkillParams[0]?.agentId === "research", "skill invocation should include agentId=research after /agent use");

await executeBuiltinSlashCommand("/agent current", { ctx });
assert(rendered.join("\n").includes('"targetAgentId": "research"'), "/agent current should render research");

await executeBuiltinSlashCommand("/agent clear-use", { ctx });
assert(session.targetAgentId === undefined, "/agent clear-use should clear targetAgentId");
session.cancel();
await waitFor(() => cancelParams.length >= 2, "main cancel notification should be observed");
assert(cancelParams[1]?.sessionId === "sess-1", "main cancel should use the primary CLI session id");
assert(!("agentId" in (cancelParams[1] ?? {})), "main cancel should omit agentId after /agent clear-use");

await session.prompt("hello main", () => undefined);
assert(!("agentId" in (promptParams[1] ?? {})), "ordinary prompt should omit agentId after /agent clear-use");

client.close();
await new Promise<void>((resolve) => server.close(() => resolve()));

console.log("probe=agent_use_target");
console.log("command=/agent use research result=PASS target=research");
console.log(`command=/agent use research autostart=PASS agentId=${String(startParams[0]?.agentId)}`);
console.log(`command=session_prompt text=\"hello research\" result=PASS sessionId=${String(promptParams[0]?.sessionId)} agentId=${String(promptParams[0]?.agentId)}`);
console.log(`command=session_cancel result=PASS sessionId=${String(cancelParams[0]?.sessionId)} agentId=${String(cancelParams[0]?.agentId)}`);
console.log(`command=session_activate_skill skill=reviewer result=PASS agentId=${String(activateSkillParams[0]?.agentId)}`);
console.log("command=/agent current result=PASS target=research");
console.log("command=/agent clear-use result=PASS target=main");
console.log(`command=session_cancel result=PASS sessionId=${String(cancelParams[1]?.sessionId)} agentId=${String(cancelParams[1]?.agentId ?? "<omitted>")}`);
console.log(`command=session_prompt text=\"hello main\" result=PASS agentId=${String(promptParams[1]?.agentId ?? "<omitted>")}`);
console.log("result=PASS");
