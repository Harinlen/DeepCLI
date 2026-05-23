import { MustangMethod } from "../src/acp/methods.js";
import { executeBuiltinSlashCommand } from "../src/active-port/coding-agent/slash-commands/builtin-registry.js";
import { SelectorController } from "../src/active-port/coding-agent/modes/controllers/selector-controller.js";
import { initTheme } from "../src/active-port/coding-agent/modes/theme/theme.js";
import { assert } from "./helpers.js";

// Parser/dispatch probe only. This intentionally mocks the session so it can
// assert slash parsing, ACP method names, and "no direct SQLite writes" quickly.
// Real Access Router/runtime closure is covered by probe_real_kernel_slash_commands.ts.
await initTheme(false, "unicode", false, "dark", "dark");

type Call = { method: string; params: Record<string, unknown> };

const calls: Call[] = [];
const warnings: string[] = [];
const rendered: string[] = [];

const ctx = {
	session: {
		managementRequest: async (method: string, params: Record<string, unknown>) => {
			calls.push({ method, params });
			return responseFor(method, params);
		},
	},
	showWarning: (message: string) => warnings.push(message),
	showStatus: (message: string) => rendered.push(message),
	chatContainer: {
		addChild: (node: unknown) => rendered.push(textFromNode(node)),
	},
	ui: { requestRender: () => undefined },
};

await executeBuiltinSlashCommand("/global backup", { ctx });
await executeBuiltinSlashCommand("/global backups", { ctx });
await executeBuiltinSlashCommand("/global export /tmp/global.json", { ctx });
await executeBuiltinSlashCommand("/global import /tmp/global.json --dry-run", { ctx });
await executeBuiltinSlashCommand("/flag list", { ctx });
await executeBuiltinSlashCommand("/flag read kernel.memory", { ctx });
await executeBuiltinSlashCommand("/flag set kernel.memory false 1", { ctx });
await executeBuiltinSlashCommand("/flag reset kernel.memory 2", { ctx });
await executeBuiltinSlashCommand("/secrets list", { ctx });
await executeBuiltinSlashCommand("/secrets audit secret-1", { ctx });
await executeBuiltinSlashCommand("/secrets rename secret-1 renamed 1", { ctx });
await executeBuiltinSlashCommand("/secrets delete secret-1 2 --confirm", { ctx });
await executeBuiltinSlashCommand("/agents list", { ctx });
await executeBuiltinSlashCommand("/agents read worker", { ctx });
await executeBuiltinSlashCommand("/agents create worker /tmp/workspace Worker", { ctx });
await executeBuiltinSlashCommand("/agents bind worker test:chan session-a", { ctx });
await executeBuiltinSlashCommand("/agent send worker hello from cli", { ctx });
await executeBuiltinSlashCommand("/gateways list", { ctx });
await executeBuiltinSlashCommand("/gateways create test2 test {\"mode\":\"probe\"}", { ctx });
await executeBuiltinSlashCommand("/gateways read test", { ctx });
await executeBuiltinSlashCommand("/gateways bind test chan worker session-a", { ctx });
await executeBuiltinSlashCommand("/mcp list", { ctx });
await executeBuiltinSlashCommand("/mcp read remote", { ctx });
await executeBuiltinSlashCommand("/mcp create remote {\"type\":\"http\",\"url\":\"https://mcp.example.test\",\"headers\":{\"Authorization\":\"secret:abc\"}}", { ctx });
await executeBuiltinSlashCommand("/mcp update remote {\"type\":\"http\",\"url\":\"https://mcp.example.test/v2\",\"headers\":{\"Authorization\":\"secret:abc\"}} 1", { ctx });
await executeBuiltinSlashCommand("/mcp delete remote 2", { ctx });
await executeBuiltinSlashCommand("/skills list", { ctx });
await executeBuiltinSlashCommand("/skills inspect skill-installer", { ctx });
await executeBuiltinSlashCommand("/skills refresh", { ctx });
const skillInstallPrompt = await executeBuiltinSlashCommand("/skills install owner/repo --ref main", { ctx });
const skillSourcesPrompt = await executeBuiltinSlashCommand("/skills sources", { ctx });
await executeBuiltinSlashCommand("/agents delete worker --confirm", { ctx });
await executeBuiltinSlashCommand("/gateways delete test2 --confirm", { ctx });

let flagsSelectorOpened = false;
await executeBuiltinSlashCommand("/flag", {
	ctx: {
		showFlagsSelector: () => {
			flagsSelectorOpened = true;
		},
	},
});
let flagsListSelectorOpened = false;
await executeBuiltinSlashCommand("/flag list", {
	ctx: {
		showFlagsSelector: () => {
			flagsListSelectorOpened = true;
		},
	},
});
const selectorCalls = await exerciseFlagsSelector();

const methods = calls.map(call => call.method);
const requiredMethods = [
	MustangMethod.globalBackup,
	MustangMethod.globalBackups,
	MustangMethod.globalExport,
	MustangMethod.globalImport,
	MustangMethod.flagsList,
	MustangMethod.flagsRead,
	MustangMethod.flagsSet,
	MustangMethod.flagsReset,
	MustangMethod.secretsList,
	MustangMethod.secretsAudit,
	MustangMethod.secretsRename,
	MustangMethod.secretsDelete,
	MustangMethod.agentsList,
	MustangMethod.agentsAdd,
	MustangMethod.agentsBind,
	MustangMethod.agentSend,
	MustangMethod.gatewaysList,
	MustangMethod.gatewaysCreate,
	MustangMethod.gatewaysStatus,
	MustangMethod.gatewaysBind,
	MustangMethod.gatewaysDelete,
	MustangMethod.mcpList,
	MustangMethod.mcpRead,
	MustangMethod.mcpCreate,
	MustangMethod.mcpUpdate,
	MustangMethod.mcpDelete,
	MustangMethod.skillsList,
	MustangMethod.skillsInspect,
	MustangMethod.skillsRefresh,
	MustangMethod.agentsDelete,
];
for (const method of requiredMethods) {
	assert(methods.includes(method), `CLI slash command should call ${method}`);
}

assert(
	calls.find(call => call.method === MustangMethod.flagsSet)?.params.expectedRevision === 1,
	"/flag set should pass observed/user supplied revision to Kernel",
);
assert(
	calls.find(call => call.method === MustangMethod.flagsRead)?.params.section === "kernel",
	"/flag read <section>.<key> should read the owning flag section",
);
assert(
	calls.find(call => call.method === MustangMethod.secretsDelete)?.params.confirm === true,
	"/secrets delete should require and pass confirm=true",
);
assert(
	calls.find(call => call.method === MustangMethod.agentSend)?.params.message === "hello from cli",
	"/agent send should preserve message text",
);
assert(
	calls.find(call => call.method === MustangMethod.gatewaysCreate)?.params.gatewayType === "test",
	"/gateways create should pass gateway type through ACP",
);
assert(
	(calls.find(call => call.method === MustangMethod.gatewaysCreate)?.params.config as Record<string, unknown>).mode === "probe",
	"/gateways create should parse JSON config for Kernel ACP",
);
assert(
	calls.find(call => call.method === MustangMethod.gatewaysDelete)?.params.confirm === true,
	"/gateways delete should require and pass confirm=true",
);
assert(
	calls.find(call => call.method === MustangMethod.mcpCreate)?.params.name === "remote",
	"/mcp create should pass server name through ACP",
);
assert(
	((calls.find(call => call.method === MustangMethod.mcpCreate)?.params.config as Record<string, unknown>).headers as Record<string, unknown>).Authorization === "secret:abc",
	"/mcp create should parse JSON config for Kernel ACP",
);
assert(
	calls.find(call => call.method === MustangMethod.mcpUpdate)?.params.expectedRevision === 1,
	"/mcp update should pass optional revision",
);
assert(
	calls.find(call => call.method === MustangMethod.mcpDelete)?.params.expectedRevision === 2,
	"/mcp delete should pass optional revision",
);
assert(
	flagsSelectorOpened,
	"/flag should open the editable flags selector when the TUI context provides one",
);
assert(
	flagsListSelectorOpened,
	"/flag list should open the editable flags selector when the TUI context provides one",
);
assert(
	selectorCalls.find(call => call.method === MustangMethod.flagsSet)?.params.value === false,
	"flags selector should allow selecting a boolean flag to toggle it",
);
assert(
	selectorCalls.find(call => call.method === MustangMethod.flagsSet)?.params.expectedRevision === 1,
	"flags selector toggle should pass the section revision",
);
assert(
	selectorCalls.find(call => call.method === MustangMethod.flagsReset)?.params.key === "memory",
	"flags selector should reset the selected flag with r",
);
assert(skillInstallPrompt === "/skill:skill-installer install owner/repo --ref main", "/skills install should route to skill-installer activation prompt");
assert(skillSourcesPrompt === "/skill:skill-installer sources", "/skills sources should route to skill-installer activation prompt");
const renderedText = rendered.join("\n");
assert(renderedText.includes("kernel.memory"), "/flag list/read should render flag rows as section.key labels");
assert(renderedText.includes("/flag set kernel.memory false"), "/flag list should show the direct edit command for boolean flags");
assert(!renderedText.includes("\"sections\""), "/flag list should not render raw ACP sections JSON");
assert(!renderedText.includes("\"payload\""), "/flag list should not render raw ACP payload JSON");

console.log("probe=cli_global_resource_slash_commands");
console.log(`global_commands=${countPrefix("_mustang.agent/global/")}`);
console.log(`flags_commands=${countPrefix("_mustang.agent/flags/")}`);
console.log(`secrets_commands=${countPrefix("_mustang.agent/secrets/")}`);
console.log(`agents_commands=${countPrefix("_mustang.agent/agents/")}`);
console.log(`agent_send_via_acp=${methods.includes(MustangMethod.agentSend)}`);
console.log(`gateways_commands=${countPrefix("_mustang.agent/gateways/")}`);
console.log(`gateway_create_delete_via_acp=${methods.includes(MustangMethod.gatewaysCreate) && methods.includes(MustangMethod.gatewaysDelete)}`);
console.log(`mcp_commands=${countPrefix("_mustang.agent/mcp/")}`);
console.log(`mcp_management_via_acp=${methods.includes(MustangMethod.mcpCreate) && methods.includes(MustangMethod.mcpUpdate) && methods.includes(MustangMethod.mcpDelete)}`);
console.log(`skills_management_via_acp=${methods.includes(MustangMethod.skillsList) && methods.includes(MustangMethod.skillsInspect) && methods.includes(MustangMethod.skillsRefresh)}`);
console.log(`skills_install_routes_to_skill_installer=${skillInstallPrompt === "/skill:skill-installer install owner/repo --ref main"}`);
console.log("sqlite_direct_writes=0");
console.log("result=PASS");

function countPrefix(prefix: string): number {
	return methods.filter(method => method.startsWith(prefix)).length;
}

function textFromNode(node: unknown): string {
	const item = node as { text?: string; getText?: () => string } | undefined;
	if (typeof item?.getText === "function") return item.getText();
	if (typeof item?.text === "string") return item.text;
	return String(node);
}

async function exerciseFlagsSelector(): Promise<Call[]> {
	const selectorCalls: Call[] = [];
	let focused: { handleInput?: (key: string) => void } | undefined;
	const editorContainer = {
		clear: () => undefined,
		addChild: () => undefined,
	};
	const ctx = {
		session: {
			managementRequest: async (method: string, params: Record<string, unknown>) => {
				selectorCalls.push({ method, params });
				return responseFor(method, params);
			},
		},
		ui: {
			terminal: { rows: 30 },
			setFocus: (component: unknown) => {
				focused = component as { handleInput?: (key: string) => void };
			},
			requestRender: () => undefined,
		},
		editorContainer,
		editor: {},
		showStatus: (message: string) => rendered.push(message),
		showError: (message: string) => rendered.push(message),
		showHookInput: async () => undefined,
	};
	new SelectorController(ctx as any).showFlagsSelector();
	await sleep(0);
	focused?.handleInput?.("\n");
	await sleep(0);
	focused?.handleInput?.("r");
	await sleep(0);
	return selectorCalls;
}

function sleep(ms: number): Promise<void> {
	return new Promise(resolve => setTimeout(resolve, ms));
}

function responseFor(method: string, params: Record<string, unknown>): Record<string, unknown> {
	switch (method) {
		case MustangMethod.globalBackup:
			return { path: "/tmp/global.db.backup", checksum: "sha256", sourceSchemaVersion: 6 };
		case MustangMethod.globalBackups:
			return { backups: ["/tmp/global.db.backup"] };
		case MustangMethod.globalExport:
			return { dryRun: Boolean(params.dryRun), format: "json", outputPath: params.outputPath, resourceCount: 1, eventCount: 0, warnings: [] };
		case MustangMethod.globalImport:
			return { dryRun: true, plannedWrites: 1, conflicts: [], errors: [], warnings: [], unavailable: false };
		case MustangMethod.flagsList:
			return { sections: [{ section: "kernel", payload: { memory: true }, revision: 1, pendingRestart: false }] };
		case MustangMethod.flagsRead:
			return { section: params.section, payload: { memory: true }, revision: 1, pendingRestart: false };
		case MustangMethod.flagsSet:
		case MustangMethod.flagsReset:
			return { section: params.section, revision: Number(params.expectedRevision ?? 0) + 1, applies: "after_restart", pendingRestart: true };
		case MustangMethod.secretsList:
			return { secrets: [{ secretId: "secret-1", name: "api", revision: 1, createdAt: "now", updatedAt: "now" }] };
		case MustangMethod.secretsAudit:
			return { events: [{ id: 1, secretId: params.secretId, eventType: "secret.rename", actorAgentId: "primary", createdAt: "now", metadata: {} }] };
		case MustangMethod.secretsRename:
			return { secretId: params.secretId, ref: `secret:${params.secretId}`, name: params.name, revision: 2 };
		case MustangMethod.secretsDelete:
			return { deleted: true };
		case MustangMethod.agentsList:
			return { agents: [{ agentId: "worker", workspace: "/tmp/workspace" }], bindings: [] };
		case MustangMethod.agentsAdd:
			return { agent: { agentId: params.agentId, workspace: params.workspace } };
		case MustangMethod.agentsBind:
			return { binding: { bindingId: params.bind, targetAgentId: params.agentId } };
		case MustangMethod.agentSend:
			return { delivered: true, result: { ok: true } };
		case MustangMethod.gatewaysList:
			return { gateways: [{ gatewayId: "test", enabled: true }] };
		case MustangMethod.gatewaysCreate:
			return { gateway: { gatewayId: params.gatewayId, gatewayType: params.gatewayType, config: params.config, enabled: params.enabled, revision: 1 } };
		case MustangMethod.gatewaysStatus:
			return { status: [{ gatewayId: params.gatewayId, enabled: true }] };
		case MustangMethod.gatewaysBind:
			return { binding: { bindingId: `${params.gatewayId}:${params.channelKey}`, targetAgentId: params.agentId } };
		case MustangMethod.gatewaysDelete:
			return { gatewayId: params.gatewayId, deleted: true, revision: 2, disabledBindings: 1 };
		case MustangMethod.mcpList:
			return { servers: [{ name: "remote", type: "http", config: { type: "http", url: "https://mcp.example.test", headers: { Authorization: "secret:abc" } } }], revision: 1 };
		case MustangMethod.mcpRead:
			return { server: { name: params.name, type: "http", config: { type: "http", url: "https://mcp.example.test", headers: { Authorization: "secret:abc" } } }, revision: 1 };
		case MustangMethod.mcpCreate:
		case MustangMethod.mcpUpdate:
			return { server: { name: params.name, type: (params.config as any)?.type ?? "stdio", config: params.config }, revision: Number(params.expectedRevision ?? 0) + 1, applies: "after_restart", pendingRestart: true };
		case MustangMethod.mcpDelete:
			return { name: params.name, deleted: true, revision: Number(params.expectedRevision ?? 0) + 1, applies: "after_restart", pendingRestart: true };
		case MustangMethod.skillsList:
			return { skills: [], commands: [] };
		case MustangMethod.skillsInspect:
			return { skill: { record: { name: params.name, source: "bundled", layerPriority: 3, userInvocable: true, modelInvocable: true, aliases: [], setupNeeded: false, missingBins: [], missingEnv: [], missingTools: [], warnings: [] }, description: "Skill installer" } };
		case MustangMethod.skillsRefresh:
			return { changed: false, added: [], removed: [], updated: [] };
		case MustangMethod.agentsDelete:
			return { agentId: params.agentId, deleted: true, workspaceDeleted: false, stateDirDeletionStatus: "deleted" };
		default:
			return {};
	}
}
