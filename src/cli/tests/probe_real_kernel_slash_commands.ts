import { spawn, type ChildProcess } from "node:child_process";
import { createServer as createHttpServer } from "node:http";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createServer as createNetServer } from "node:net";
import { AcpClient } from "../src/acp/client.js";
import { executeBuiltinSlashCommand } from "../src/active-port/coding-agent/slash-commands/builtin-registry.js";
import { BUILTIN_SLASH_COMMANDS } from "../src/active-port/coding-agent/extensibility/slash-commands.js";
import { initTheme } from "../src/active-port/coding-agent/modes/theme/theme.js";
import { MustangMethod } from "../src/acp/methods.js";
import { ModelService } from "../src/models/service.js";
import { MustangSession } from "../src/session.js";
import { assert } from "./helpers.js";

const repoRoot = resolve(import.meta.dir, "../../..");
const runKernel = join(repoRoot, "scripts", "run-kernel.sh");

const tempRoot = mkdtempSync(join(tmpdir(), "deepcli-real-slash-"));
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
	kernel = startKernel();
	await waitReady(port);
	client = await AcpClient.connect(url, readToken());
	const session = new MustangSession(client, "real-slash-probe");
	const sessionFacade = makeSessionFacade(client, session);
	const errors: string[] = [];
	const warnings: string[] = [];
	const rendered: string[] = [];
	const statuses: string[] = [];
	const returnedPrompts: string[] = [];
	const ctx = {
		session: sessionFacade,
		showError: (message: string) => errors.push(message),
		showWarning: (message: string) => warnings.push(message),
		showStatus: (message: string) => statuses.push(message),
		chatContainer: {
			addChild: (node: unknown) => rendered.push(textFromNode(node)),
		},
		ui: { requestRender: () => undefined },
		handleCompactCommand: async () => statuses.push("compact"),
		handleUsageCommand: async () => statuses.push("cost"),
		handleMemoryCommand: async (command: string) => statuses.push(command),
		handleClearCommand: async () => statuses.push("clear"),
		handleHotkeysCommand: async () => statuses.push("help"),
		shutdown: async () => statuses.push("shutdown"),
		syncPlanModeWithPermissionMode: async (mode: string) => statuses.push(`sync-plan:${mode}`),
		enterPlanModeCommand: async () => {
			sessionFacade.currentPermissionMode = "plan";
			statuses.push("plan-enter");
		},
		exitPlanModeCommand: async () => {
			sessionFacade.currentPermissionMode = "default";
			statuses.push("plan-exit");
		},
		statusLine: { invalidate: () => undefined },
		updateEditorTopBorder: () => undefined,
		updateEditorBorderColor: () => undefined,
		showSessionSelector: () => statuses.push("session-selector"),
		refreshWelcomeRecentSessions: async () => undefined,
		showModelSelector: () => statuses.push("model-selector"),
		showModelAdd: () => statuses.push("model-add"),
		showWebFetchBackendSelector: () => statuses.push("webfetch-backend-selector"),
		showWebFetchConfigSelector: () => statuses.push("webfetch-config-selector"),
		showThemeSelector: () => statuses.push("theme-selector"),
		enableThemeWatcher: false,
	};

	const commands = await smokeCommands(client, tempRoot, workspace);
	assertBuiltinSlashCoverage(commands);
	const commandCatalog = await session.listCommands();
	const skillCommands = skillCommandsFromCatalog(commandCatalog);
	await assertSkillCommandPrintActivation(skillCommands);
	for (const command of commands) {
		const result = await executeBuiltinSlashCommand(command, { ctx });
		if (typeof result === "string") returnedPrompts.push(result);
	}

	assert(errors.length === 0, `real slash commands should not render errors: ${errors.join("\n")}`);
	assert(
		rendered.some(line => line.includes("Kernel runtime")),
		"/kernel status should render supervisor runtime status",
	);
	assert(
		rendered.some(line => line.includes("skill-installer")),
		"/skills list or inspect should render bundled skill-installer",
	);
	assert(
		returnedPrompts.includes("/skill:skill-installer install owner/repo --ref main"),
		"/skills install should route to the skill-installer prompt",
	);

	console.log("probe=real_kernel_slash_commands");
	console.log("kernel_status_via_real_acp=true");
	console.log("skills_management_via_real_acp=true");
	console.log(`skill_commands_via_real_cli_print=${skillCommands.length}`);
	console.log("top_level_slash_commands_smoked=true");
	console.log(`warnings=${warnings.length}`);
	for (const warning of warnings) console.log(`warning=${warning}`);
	console.log("result=PASS");
} finally {
	client?.close();
	if (kernel) await stopKernel(kernel).catch(() => {});
	await fakeLlm?.stop();
	rmSync(tempRoot, { recursive: true, force: true });
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

async function smokeCommands(
	client: AcpClient,
	tempRoot: string,
	workspace: string,
): Promise<string[]> {
	const exportPath = join(tempRoot, "global-export.json");
	const secret = await seedSecretFixture(client);
	return [
		"/clear",
		"/compact",
		"/cost",
		"/memory list",
		"/help",
		"/theme current",
		"/theme list",
		"/theme set dark",
		"/plan status",
		"/plan enter",
		"/plan status",
		"/plan exit",
		"/session info",
		"/session current",
		"/session list",
		"/session new",
		"/session switch 1",
		"/session load 1",
		"/session rename Probe Session",
		"/session archive",
		"/session unarchive",
		"/model current",
		"/model list",
		"/model add",
		"/model use real_slash_fake/real-slash-model",
		"/webfetch backend",
		"/webfetch backend auto",
		"/webfetch config",
		"/webfetch config httpx.timeout 10",
		"/kernel status",
		"/global backup",
		"/global backups",
		`/global export ${exportPath}`,
		`/global import ${exportPath} --dry-run`,
		"/flag list",
		"/flag read kernel.memory",
		"/flag set kernel.memory false",
		"/flag reset kernel.memory",
		"/secrets list",
		"/secrets audit",
		`/secrets rename ${secret.secretId} probe-renamed ${secret.revision}`,
		`/secrets delete ${secret.secretId} ${secret.revision + 1} --confirm`,
		`/agents create worker ${workspace} Worker`,
		"/agents list",
		"/agents read worker",
		"/gateways create testgw test {}",
		"/gateways list",
		"/gateways read testgw",
		"/gateways status testgw",
		"/gateways bind testgw chan1 worker",
		"/agents bind worker testgw:chan2",
		"/agent send primary hello",
		"/mcp create remote {\"type\":\"http\",\"url\":\"https://mcp.example.test\",\"headers\":{\"Authorization\":\"secret:abc\"}}",
		"/mcp list",
		"/mcp read remote",
		"/mcp update remote {\"type\":\"http\",\"url\":\"https://mcp.example.test/v2\",\"headers\":{\"Authorization\":\"secret:abc\"}}",
		"/mcp delete remote",
		"/skills list",
		"/skills inspect skill-installer",
		"/skills refresh",
		"/skills install owner/repo --ref main",
		"/skills sources",
		"/skills search test",
		"/skills check skill-installer",
		"/skills update skill-installer",
		"/skills audit",
		"/skills uninstall skill-installer",
		"/agents delete worker --confirm",
		"/gateways delete testgw --confirm",
		"/session delete confirm",
		"/kernel restart",
		"/quit",
		"/exit",
	];
}

async function seedSecretFixture(client: AcpClient): Promise<{ secretId: string; revision: number }> {
	await client.request("_mustang.agent/secrets/auth", {
		action: "set",
		name: "probe-secret",
		value: "probe-secret-value",
		kind: "static",
	});
	const result = await client.request<{ secrets?: Array<{ secretId?: string; secret_id?: string; revision?: number }> }>(
		MustangMethod.secretsList,
		{},
	);
	const secret = (result.secrets ?? []).find(item => (item.secretId ?? item.secret_id));
	assert(secret !== undefined, "secret fixture should be visible through /secrets list");
	const secretId = secret.secretId ?? secret.secret_id;
	assert(secretId !== undefined && secretId.length > 0, "secret fixture should include a secret id");
	assert(Number.isInteger(secret.revision), "secret fixture should include a revision");
	return { secretId, revision: secret.revision ?? 1 };
}

function assertBuiltinSlashCoverage(commands: string[]): void {
	const smoked = new Set(
		commands
			.filter(command => command.startsWith("/") && !command.startsWith("/skill:"))
			.map(command => command.slice(1).split(/\s+/, 1)[0])
			.filter(Boolean),
	);
	const missing = BUILTIN_SLASH_COMMANDS
		.map(command => command.name)
		.filter(name => !smoked.has(name));
	assert(
		missing.length === 0,
		`real kernel slash smoke must cover every builtin slash command; missing=${missing.join(",")}`,
	);
}

function skillCommandsFromCatalog(commands: Array<{ name?: string; source?: string }>): string[] {
	const names = commands
		.filter(command => command.source === "skill")
		.map(command => command.name ?? "")
		.filter(name => name.startsWith("skill:"))
		.sort();
	assert(names.length > 0, "runtime command catalog should expose at least one skill command");
	assert(
		names.includes("skill:skill-installer"),
		"runtime command catalog should expose the bundled skill-installer command",
	);
	return names;
}

async function assertSkillCommandPrintActivation(commands: string[]): Promise<void> {
	for (const command of commands) {
		const output = await runCliPrint(`/${command} smoke`);
		assert(
			output.includes("REAL_KERNEL_SKILL_OK"),
			`/${command} should run through real CLI print activation path`,
		);
	}
}

function makeSessionFacade(client: AcpClient, session: MustangSession): any {
	let currentPermissionMode = "default";
	const modelService = new ModelService(client);
	return Object.assign(session, {
		get currentPermissionMode() {
			return currentPermissionMode;
		},
		set currentPermissionMode(mode: string) {
			currentPermissionMode = mode;
		},
		createSession: async () => {
			const created = await MustangSession.create(client, workspace);
			return created.sessionId;
		},
		listSessions: async (limit = 50) => {
			const result = await client.request<{ sessions?: Array<Record<string, unknown>> }>("session/list", { limit });
			return result.sessions ?? [];
		},
		loadSession: async (target: string) => {
			await MustangSession.load(client, target, workspace);
			return target;
		},
		sessionManager: {
			setSessionName: async () => undefined,
		},
		archiveCurrentSession: async () => undefined,
		deleteCurrentSessionAndCreate: async () => "real-slash-probe-after-delete",
		setPermissionMode: async (mode: string) => {
			currentPermissionMode = mode;
		},
		listProviderModels: async () => await modelService.listProviders(),
		setCurrentModelRole: async (role: string, provider: string, model: string) => {
			await client.request(MustangMethod.modelSetCurrent, { role, provider, model });
			return true;
		},
		setWebFetchBackend: async (backend: string, runSetup = false, apiKey?: string) => (
			await client.request(MustangMethod.webFetchSetBackend, {
				backend,
				runSetup,
				...(apiKey ? { apiKey } : {}),
			})
		),
		setWebFetchConfig: async (path: string, value: unknown) => (
			await client.request(MustangMethod.webFetchSetConfig, { path, value })
		),
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
			"    real_slash_fake:",
			"      type: openai_compatible",
			`      base_url: ${baseUrl}`,
			"      api_key: real-slash-test",
			"      models:",
			"        - real-slash-model",
			"  current_used:",
			"    default:",
			"      - real_slash_fake",
			"      - real-slash-model",
			"    compact:",
			"      - real_slash_fake",
			"      - real-slash-model",
			"",
		].join("\n"),
		"utf8",
	);
}

async function startFakeOpenAIServer(): Promise<{ baseUrl: string; stop: () => Promise<void> }> {
	const server = createHttpServer((request, response) => {
		if (request.method === "GET" && request.url?.endsWith("/models")) {
			const body = JSON.stringify({ data: [{ id: "real-slash-model" }] });
			response.writeHead(200, {
				"Content-Type": "application/json",
				"Content-Length": Buffer.byteLength(body),
			});
			response.end(body);
			return;
		}
		if (request.method === "POST" && request.url?.endsWith("/chat/completions")) {
			request.resume();
			response.writeHead(200, {
				"Content-Type": "text/event-stream",
				"Cache-Control": "no-cache",
			});
			for (const payload of [
				{ choices: [{ delta: { content: "REAL_KERNEL_SKILL_OK" }, finish_reason: null }] },
				{ choices: [{ delta: {}, finish_reason: "stop" }] },
			]) {
				response.write(`data: ${JSON.stringify(payload)}\n\n`);
			}
			response.write("data: [DONE]\n\n");
			response.end();
			return;
		}
		response.writeHead(404);
		response.end();
	});
	await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
	const address = server.address();
	if (!address || typeof address === "string") throw new Error("Could not start fake LLM");
	return {
		baseUrl: `http://127.0.0.1:${address.port}/v1`,
		stop: async () => {
			await new Promise<void>(resolve => server.close(() => resolve()));
		},
	};
}

async function runCliPrint(prompt: string): Promise<string> {
	return await new Promise((resolve, reject) => {
		const child = spawn("bun", ["run", "src/main.ts", "--print", prompt], {
			cwd: join(repoRoot, "src/cli"),
			env: {
				...process.env,
				KERNEL_URL: url,
				DEEPCLI_TOKEN: readToken(),
				DEEPCLI_HOME: tempRoot,
				DEEPCLI_STATE_DIR: stateDir,
				DEEPCLI_CONFIG_DIR: join(tempRoot, "config"),
			},
			stdio: ["ignore", "pipe", "pipe"],
		});
		let output = "";
		child.stdout.on("data", chunk => {
			output += chunk.toString();
		});
		child.stderr.on("data", chunk => {
			output += chunk.toString();
		});
		const timer = setTimeout(() => {
			child.kill("SIGKILL");
			reject(new Error(`CLI print timed out:\n${output}`));
		}, 30_000);
		child.once("close", code => {
			clearTimeout(timer);
			if (code === 0) resolve(output);
			else reject(new Error(`CLI print failed with ${code}:\n${output}`));
		});
	});
}
