import { MustangMethod } from "../src/acp/methods.js";
import { executeBuiltinSlashCommand } from "../src/active-port/coding-agent/slash-commands/builtin-registry.js";
import { initTheme } from "../src/active-port/coding-agent/modes/theme/theme.js";
import { assert } from "./helpers.js";

await initTheme(false, "unicode", false, "dark", "dark");

type RequestCall = { method: string; params: Record<string, unknown> };

function makeCtx() {
	const requests: RequestCall[] = [];
	const calls: string[] = [];
	const ctx: any = {
		session: {
			currentPermissionMode: "default",
			managementRequest: async (method: string, params: Record<string, unknown>) => {
				requests.push({ method, params });
				if (method === MustangMethod.flagsRead) {
					return { section: params.section, revision: 7, payload: { enabled: true } };
				}
				if (method === MustangMethod.agentsList) {
					return { agents: [{ agentId: "worker" }, { agent_id: "other" }] };
				}
				if (method === MustangMethod.agentsHealth) {
					return { health: { agentId: params.agentId, routeStatus: "registered" } };
				}
				return { ok: true, method, params };
			},
			sessionManager: { setSessionName: async (title: string) => calls.push(`rename:${title}`) },
			listSessions: async () => [{ sessionId: "one" }, { sessionId: "two" }],
			loadSession: async (id: string) => calls.push(`load:${id}`),
			createSession: async () => "new-session",
			archiveCurrentSession: async (archive: boolean) => calls.push(`archive:${archive}`),
			deleteCurrentSessionAndCreate: async () => "next-session",
			listProviderModels: async () => ({ models: [{ providerName: "p", modelId: "m" }], currentUsed: {} }),
			setCurrentModelRole: async (role: string, provider: string, model: string) => calls.push(`model:${role}:${provider}/${model}`),
			setWebFetchBackend: async (backend: string, setup: boolean) => ({ message: `${backend}:${setup}` }),
			setWebFetchConfig: async (key: string, value: unknown) => ({ backend: "httpx", backends: { httpx: { [key]: value } } }),
			runtimeStatus: async () => ({ status: { ready: true } }),
			runtimeRestart: async () => ({ status: { status: "ready" } }),
		},
		chatContainer: { addChild: () => calls.push("chat") },
		ui: { requestRender: () => calls.push("render"), invalidate: () => calls.push("ui-invalidate") },
		statusLine: { invalidate: () => calls.push("status-invalidate") },
		showStatus: (message: string) => calls.push(`status:${message}`),
		showWarning: (message: string) => calls.push(`warning:${message}`),
		showError: (message: string) => calls.push(`error:${message}`),
		showSessionSelector: () => calls.push("session-selector"),
		showModelSelector: () => calls.push("model-selector"),
		showModelAdd: () => calls.push("model-add"),
		showWebFetchBackendSelector: () => calls.push("webfetch-backend-selector"),
		showWebFetchConfigSelector: () => calls.push("webfetch-config-selector"),
		showHookInput: async () => "secret-value",
		handleSessionCommand: async () => calls.push("session-info"),
		handleHotkeysCommand: () => calls.push("hotkeys"),
		handleCompactCommand: () => calls.push("compact"),
		handleUsageCommand: () => calls.push("cost"),
		syncPlanModeWithPermissionMode: async (mode: string) => calls.push(`sync:${mode}`),
		enterPlanModeCommand: async (prompt?: string) => calls.push(`plan-enter:${prompt ?? ""}`),
		exitPlanModeCommand: async () => calls.push("plan-exit"),
		refreshWelcomeRecentSessions: async () => calls.push("refresh"),
		updateEditorTopBorder: () => calls.push("top-border"),
		updateEditorBorderColor: () => calls.push("border"),
	};
	return { ctx, requests, calls };
}

async function assertRequest(command: string, method: string, params: Record<string, unknown>) {
	const { ctx, requests } = makeCtx();
	const result = await executeBuiltinSlashCommand(command, { ctx });
	assert(result === true, `${command} should be consumed`);
	const last = requests.at(-1);
	assert(last?.method === method, `${command} should call ${method}, got ${last?.method}`);
	assert(JSON.stringify(last.params) === JSON.stringify(params), `${command} params mismatch: ${JSON.stringify(last.params)}`);
}

await assertRequest("/global backup /tmp/out", MustangMethod.globalBackup, { outputDir: "/tmp/out" });
await assertRequest("/global backups /tmp/backups", MustangMethod.globalBackups, { backupDir: "/tmp/backups" });
await assertRequest("/global export /tmp/global.json --dry-run", MustangMethod.globalExport, { outputPath: "/tmp/global.json", dryRun: true });
await assertRequest("/global import /tmp/global.json --dry-run", MustangMethod.globalImport, { inputPath: "/tmp/global.json", dryRun: true });
await assertRequest("/global restore backup-1 --confirm", MustangMethod.globalRestore, { backupIdOrPath: "backup-1", confirm: true });

await assertRequest("/flag list", MustangMethod.flagsList, {});
await assertRequest("/flag set kernel.enabled false 9", MustangMethod.flagsSet, {
	section: "kernel",
	key: "enabled",
	value: false,
	expectedRevision: 9,
});
await assertRequest("/flag reset kernel.enabled 9", MustangMethod.flagsReset, {
	section: "kernel",
	key: "enabled",
	expectedRevision: 9,
});

await assertRequest("/secrets list", MustangMethod.secretsList, {});
await assertRequest("/secrets audit secret-1", MustangMethod.secretsAudit, { secretId: "secret-1" });
await assertRequest("/secrets rename secret-1 renamed 3", MustangMethod.secretsRename, {
	secretId: "secret-1",
	name: "renamed",
	expectedRevision: 3,
});
await assertRequest("/secrets delete secret-1 3 --confirm", MustangMethod.secretsDelete, {
	secretId: "secret-1",
	expectedRevision: 3,
	confirm: true,
});

await assertRequest("/agent list --bindings", MustangMethod.agentsList, { includeBindings: true });
await assertRequest("/agent create worker /tmp/workspace Worker Name", MustangMethod.agentsAdd, {
	agentId: "worker",
	workspace: "/tmp/workspace",
	name: "Worker Name",
});
await assertRequest("/agent send worker hello there", MustangMethod.agentSend, { agentId: "worker", message: "hello there" });
await assertRequest("/agent bind worker test:chan session-a", MustangMethod.agentsBind, {
	agentId: "worker",
	bind: "test:chan",
	sessionId: "session-a",
});
await assertRequest("/agent delete worker --confirm", MustangMethod.agentsDelete, { agentId: "worker", confirm: true });
await assertRequest("/agent grant worker tool project repo", MustangMethod.agentsGrant, {
	agentId: "worker",
	capability: "tool",
	scope: "project",
	resource: "repo",
});

{
	const { ctx, calls } = makeCtx();
	await executeBuiltinSlashCommand("/agent use worker", { ctx });
	assert(ctx.session.targetAgentId === "worker", "/agent use should set the CLI target agent");
	assert(calls.includes("status-invalidate"), "/agent use should refresh the status line");
	await executeBuiltinSlashCommand("/agent current", { ctx });
	await executeBuiltinSlashCommand("/agent clear-use", { ctx });
	assert(ctx.session.targetAgentId === undefined, "/agent clear-use should restore the main target");
}

await assertRequest("/gateways list", MustangMethod.gatewaysList, {});
await assertRequest("/gateways read gw", MustangMethod.gatewaysStatus, { gatewayId: "gw" });
await assertRequest("/gateways create gw test {\"mode\":\"probe\"}", MustangMethod.gatewaysCreate, {
	gatewayId: "gw",
	gatewayType: "test",
	config: { mode: "probe" },
	enabled: true,
});
await assertRequest("/gateways bind gw chan worker session-a", MustangMethod.gatewaysBind, {
	gatewayId: "gw",
	channelKey: "chan",
	agentId: "worker",
	sessionId: "session-a",
});
await assertRequest("/gateways delete gw --confirm", MustangMethod.gatewaysDelete, { gatewayId: "gw", confirm: true });

await assertRequest("/mcp list", MustangMethod.mcpList, {});
await assertRequest("/mcp read remote", MustangMethod.mcpRead, { name: "remote" });
await assertRequest("/mcp create remote {\"type\":\"http\"}", MustangMethod.mcpCreate, {
	name: "remote",
	config: { type: "http" },
});
await assertRequest("/mcp update remote {\"type\":\"stdio\"} 2", MustangMethod.mcpUpdate, {
	name: "remote",
	config: { type: "stdio" },
	expectedRevision: 2,
});
await assertRequest("/mcp delete remote 2", MustangMethod.mcpDelete, { name: "remote", expectedRevision: 2 });

await assertRequest("/skills list", MustangMethod.skillsList, {});
await assertRequest("/skills inspect skill-installer", MustangMethod.skillsInspect, { name: "skill-installer" });
await assertRequest("/skills refresh", MustangMethod.skillsRefresh, {});

{
	const { ctx, calls } = makeCtx();
	await executeBuiltinSlashCommand("/session list", { ctx });
	await executeBuiltinSlashCommand("/session switch 2", { ctx });
	await executeBuiltinSlashCommand("/session rename Better Title", { ctx });
	assert(calls.includes("session-selector"), "/session list should open selector");
	assert(calls.includes("load:two"), "/session switch <number> should resolve session ordinal");
	assert(calls.includes("rename:Better Title"), "/session rename should set the session name");
}

{
	const { ctx, calls } = makeCtx();
	await executeBuiltinSlashCommand("/model list", { ctx });
	await executeBuiltinSlashCommand("/model use plan p/m", { ctx });
	assert(calls.includes("model-selector"), "/model list should open model selector when models exist");
	assert(calls.includes("model:plan:p/m"), "/model use should parse role and provider/model");
}

{
	const { ctx, calls } = makeCtx();
	const prompt = await executeBuiltinSlashCommand("/skills install owner/repo --ref main", { ctx });
	assert(prompt === "/skill:skill-installer install owner/repo --ref main", "/skills install should return skill-installer prompt text");
	calls.length = 0;
	ctx.session.currentPermissionMode = "plan";
	const planPrompt = await executeBuiltinSlashCommand("/plan refine this", { ctx });
	assert(planPrompt === "refine this", "/plan <text> should become prompt text while plan mode is active");
}

{
	const { ctx, calls } = makeCtx();
	const result = await executeBuiltinSlashCommand("/does-not-exist", { ctx });
	assert(result === undefined, "unknown slash command should return undefined for InputController fallback");
	await executeBuiltinSlashCommand("/agent nonsense", { ctx });
	assert(calls.some(call => call.startsWith("warning:Usage: /agent")), "/agent unknown subcommand should warn with /agent grammar");
}

console.log("PASS: builtin slash grammar table");
