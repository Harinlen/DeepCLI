import { spawn, type ChildProcess } from "node:child_process";
import { createServer as createHttpServer } from "node:http";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createServer as createNetServer } from "node:net";
import { AcpClient } from "../src/acp/client.js";
import { MustangMethod } from "../src/acp/methods.js";
import { executeBuiltinSlashCommand } from "../src/active-port/coding-agent/slash-commands/builtin-registry.js";
import { initTheme } from "../src/active-port/coding-agent/modes/theme/theme.js";
import { MustangSession } from "../src/session.js";
import { assert } from "./helpers.js";

const repoRoot = resolve(import.meta.dir, "../../..");
const runKernel = join(repoRoot, "scripts", "run-kernel.sh");
const tempRoot = mkdtempSync(join(tmpdir(), "deepcli-agent-output-"));
const stateDir = join(tempRoot, "state");
const workspace = join(tempRoot, "workspace");
const port = await freePort();
const url = `ws://127.0.0.1:${port}`;

let kernel: ChildProcess | undefined;
let client: AcpClient | undefined;
let fakeLlm: Awaited<ReturnType<typeof startFakeOpenAIServer>> | undefined;

try {
	await initTheme(false, "unicode", false, "dark", "dark");
	fakeLlm = await startFakeOpenAIServer();
	writeKernelConfig(fakeLlm.baseUrl);
	mkdirSync(workspace, { recursive: true });
	kernel = startKernel();
	await waitReady(port);
	client = await AcpClient.connect(url, readToken());
	const session = await MustangSession.create(client, workspace);
	const sessionFacade = makeSessionFacade(client, session);
	const buckets = makeOutputBuckets();
	const ctx = makeCtx(sessionFacade, buckets);
	const grant = await seedAgentGrantFixture(client, workspace);
	await client.request(MustangMethod.gatewaysCreate, {
		gatewayId: "testgw",
		gatewayType: "test",
		config: {},
		enabled: true,
	});
	const commands = [
		`/agent create worker ${workspace} Worker`,
		"/agent list",
		"/agent read worker",
		"/agent add worker2 /tmp Worker2",
		"/agent set-identity worker WorkerRenamed",
		"/agent bindings",
		"/agent health worker",
		"/agent grants",
		"/agent grants worker",
		"/agent grant worker agent_control global",
		`/agent revoke-grant ${grant.grantId}`,
		"/agent start worker",
		"/agent stop worker",
		"/agent restart worker",
		"/agent use worker",
		"/agent current",
		"/agent clear-use",
		"/agent bind worker testgw:chan2",
		"/agent unbind worker testgw:chan2",
		"/agent send primary hello",
		"/agent delete worker --confirm",
		"/agent delete worker2 --confirm",
		`/agent delete ${grant.agentId} --confirm`,
	];

	console.log("probe=real_agent_command_outputs");
	for (const command of commands) {
		const before = snapshot(buckets);
		const returned = await executeBuiltinSlashCommand(command, { ctx });
		const after = diffBuckets(before, buckets);
		if (typeof returned === "string") after.returned.push(returned);
		assert(after.errors.length === 0, `${command} rendered errors: ${after.errors.join("\n")}`);
		console.log(`command=${command}`);
		printBlock("visible_output", after.rendered);
		printBlock("status", after.statuses);
		printBlock("warning", after.warnings);
		printBlock("error", after.errors);
		printBlock("return", after.returned);
		if (command === "/agent use worker") {
			const promptUpdates: unknown[] = [];
			const promptResult = await session.prompt("ping", (update) => promptUpdates.push(update));
			const stopReason = promptResult.stopReason ?? (promptResult as { stop_reason?: string }).stop_reason;
			assert(stopReason === "end_turn", `/agent use worker should route an ordinary prompt to the worker runtime: ${JSON.stringify(promptResult)}`);
			printBlock("prompt_after_use", [
				`targetAgentId=${session.targetAgentId ?? "main"}`,
				`stopReason=${stopReason}`,
				`updates=${promptUpdates.length}`,
			]);
			const cancelUpdates: unknown[] = [];
			const cancelPrompt = session.prompt("slow cancel target prompt", (update) => cancelUpdates.push(update));
			await sleep(150);
			session.cancel();
			const cancelResult = await cancelPrompt;
			const cancelStopReason = cancelResult.stopReason ?? (cancelResult as { stop_reason?: string }).stop_reason;
			assert(cancelStopReason === "cancelled", `/agent use worker should route cancel to the worker runtime: ${JSON.stringify(cancelResult)}`);
			printBlock("cancel_after_use", [
				`targetAgentId=${session.targetAgentId ?? "main"}`,
				`sessionId=${session.targetAgentSessionId ?? session.sessionId}`,
				`stopReason=${cancelStopReason}`,
				`updates=${cancelUpdates.length}`,
			]);
		}
		if (command === "/agent send primary hello") {
			const rendered = after.rendered.join("\n");
			assert(rendered.includes('"delivered": true'), "/agent send should report delivery");
			assert(rendered.includes('"ok": true'), "/agent send should execute the runtime turn");
			assert(!rendered.includes("unknown_runtime_method"), "/agent send must not hit an unknown runtime method");
		}
		console.log("result=PASS");
	}
	console.log(`commands_total=${commands.length}`);
	console.log(`commands_passed=${commands.length}`);
	console.log("result=PASS");
} finally {
	client?.close();
	if (kernel) await stopKernel(kernel).catch(() => {});
	await fakeLlm?.stop();
	rmSync(tempRoot, { recursive: true, force: true });
}

function makeOutputBuckets(): OutputBuckets {
	return { rendered: [], statuses: [], warnings: [], errors: [] };
}

function makeCtx(session: any, buckets: OutputBuckets): any {
	return {
		session,
		showError: (message: string) => buckets.errors.push(message),
		showWarning: (message: string) => buckets.warnings.push(message),
		showStatus: (message: string) => buckets.statuses.push(message),
		chatContainer: {
			addChild: (node: unknown) => buckets.rendered.push(textFromNode(node)),
		},
		ui: { requestRender: () => undefined },
		statusLine: { invalidate: () => undefined },
		updateEditorTopBorder: () => undefined,
		updateEditorBorderColor: () => undefined,
	};
}

type OutputBuckets = {
	rendered: string[];
	statuses: string[];
	warnings: string[];
	errors: string[];
};

type OutputSnapshot = OutputBuckets & { returned: string[] };

function snapshot(buckets: OutputBuckets): OutputSnapshot {
	return {
		rendered: [...buckets.rendered],
		statuses: [...buckets.statuses],
		warnings: [...buckets.warnings],
		errors: [...buckets.errors],
		returned: [],
	};
}

function diffBuckets(before: OutputSnapshot, after: OutputBuckets): OutputSnapshot {
	return {
		rendered: after.rendered.slice(before.rendered.length),
		statuses: after.statuses.slice(before.statuses.length),
		warnings: after.warnings.slice(before.warnings.length),
		errors: after.errors.slice(before.errors.length),
		returned: [],
	};
}

function printBlock(label: string, lines: string[]): void {
	console.log(`${label}<<`);
	if (lines.length === 0) {
		console.log("(none)");
	} else {
		for (const line of lines) console.log(line);
	}
	console.log(">>");
}

function startKernel(): ChildProcess {
	return spawn(runKernel, [
		"--access-port",
		String(port),
		"--state-dir",
		stateDir,
		"--workspace",
		workspace,
		"--dev",
	], {
		cwd: repoRoot,
		env: {
			...process.env,
			DEEPCLI_HOME: tempRoot,
			DEEPCLI_STATE_DIR: stateDir,
			DEEPCLI_CONFIG_DIR: join(tempRoot, "config"),
		},
		stdio: ["ignore", "ignore", "ignore"],
	});
}

async function stopKernel(child: ChildProcess): Promise<void> {
	if (child.exitCode !== null || child.killed) return;
	child.kill("SIGINT");
	await new Promise<void>((resolve) => {
		const timer = setTimeout(() => {
			child.kill("SIGKILL");
			resolve();
		}, 5_000);
		child.once("close", () => {
			clearTimeout(timer);
			resolve();
		});
	});
}

function readToken(): string {
	return readFileSync(join(stateDir, "auth_token"), "utf8").trim();
}

async function waitReady(port: number): Promise<void> {
	const deadline = Date.now() + 45_000;
	while (Date.now() < deadline) {
		try {
			const response = await fetch(`http://127.0.0.1:${port}/access/readiness`);
			if (response.ok) {
				const body = await response.json() as { default_route_ready?: boolean };
				if (body.default_route_ready === true) return;
			}
		} catch {
			// Keep polling until the supervised Access Router is listening.
		}
		await sleep(250);
	}
	throw new Error(`Kernel on port ${port} did not become ready`);
}

async function freePort(): Promise<number> {
	const server = createNetServer();
	await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
	const address = server.address();
	await new Promise<void>((resolve) => server.close(() => resolve()));
	if (!address || typeof address === "string") throw new Error("Could not allocate free port");
	return address.port;
}

function sleep(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

async function seedAgentGrantFixture(client: AcpClient, workspace: string): Promise<{ agentId: string; grantId: string }> {
	const agentId = "grantworker";
	await client.request(MustangMethod.agentsAdd, {
		agentId,
		workspace,
		name: "Grant Worker",
	});
	const result = await client.request<{ grant?: { grantId?: string; grant_id?: string } }>(
		MustangMethod.agentsGrant,
		{
			agentId,
			capability: "agent_control",
			scope: "global",
		},
	);
	const grantId = result.grant?.grantId ?? result.grant?.grant_id;
	assert(grantId !== undefined && grantId.length > 0, "agent grant fixture should include a grant id");
	return { agentId, grantId };
}

function makeSessionFacade(client: AcpClient, session: MustangSession): any {
	return Object.assign(session, {
		runtimeStatus: async () => ({ status: { status: "ready" } }),
		runtimeRestart: async () => ({ status: { status: "restarting" } }),
		client,
	});
}

function textFromNode(node: unknown): string {
	const item = node as { text?: string; getText?: () => string } | undefined;
	if (typeof item?.getText === "function") return item.getText();
	if (typeof item?.text === "string") return item.text;
	return String(node);
}

function writeKernelConfig(baseUrl: string): void {
	const configDir = join(tempRoot, "config");
	mkdirSync(configDir, { recursive: true });
	writeFileSync(
		join(configDir, "kernel.yaml"),
		[
			"llm:",
			"  providers:",
			"    agent_output_fake:",
			"      type: openai_compatible",
			`      base_url: ${baseUrl}`,
			"      api_key: agent-output-test",
			"      models:",
			"        - agent-output-model",
			"  current_used:",
			"    default:",
			"      - agent_output_fake",
			"      - agent-output-model",
			"    compact:",
			"      - agent_output_fake",
			"      - agent-output-model",
			"",
		].join("\n"),
		"utf8",
	);
}

async function startFakeOpenAIServer(): Promise<{ baseUrl: string; stop: () => Promise<void> }> {
	const server = createHttpServer((request, response) => {
		if (request.method === "GET" && request.url?.endsWith("/models")) {
			response.writeHead(200, { "content-type": "application/json" });
			response.end(JSON.stringify({ data: [{ id: "agent-output-model" }] }));
			return;
		}
		let body = "";
		request.on("data", (chunk) => {
			body += chunk.toString();
		});
		request.on("end", () => {
			const payload = body ? JSON.parse(body) as { stream?: boolean } : {};
			if (payload.stream) {
				response.writeHead(200, { "content-type": "text/event-stream" });
				response.write(`data: ${JSON.stringify({
					id: "chatcmpl-agent-output",
					object: "chat.completion.chunk",
					choices: [{ index: 0, delta: { content: "ok" }, finish_reason: null }],
				})}\n\n`);
				if (body.includes("slow cancel target prompt")) {
					setTimeout(() => {
						if (response.destroyed || response.writableEnded) return;
						response.write(`data: ${JSON.stringify({
							id: "chatcmpl-agent-output",
							object: "chat.completion.chunk",
							choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
						})}\n\n`);
						response.end("data: [DONE]\n\n");
					}, 5_000);
					return;
				}
				response.write(`data: ${JSON.stringify({
					id: "chatcmpl-agent-output",
					object: "chat.completion.chunk",
					choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
				})}\n\n`);
				response.end("data: [DONE]\n\n");
				return;
			}
			response.writeHead(200, { "content-type": "application/json" });
			response.end(JSON.stringify({
				id: "chatcmpl-agent-output",
				object: "chat.completion",
				choices: [{
					index: 0,
					message: { role: "assistant", content: "ok" },
					finish_reason: "stop",
				}],
				usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
			}));
		});
	});
	await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
	const address = server.address();
	if (!address || typeof address === "string") throw new Error("Could not start fake OpenAI server");
	return {
		baseUrl: `http://127.0.0.1:${address.port}/v1`,
		stop: () => new Promise<void>((resolve) => server.close(() => resolve())),
	};
}
