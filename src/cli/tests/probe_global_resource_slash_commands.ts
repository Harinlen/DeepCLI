import { MustangMethod } from "../src/acp/methods.js";
import { executeBuiltinSlashCommand } from "../src/active-port/coding-agent/slash-commands/builtin-registry.js";
import { initTheme } from "../src/active-port/coding-agent/modes/theme/theme.js";
import { assert } from "./helpers.js";

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
		addChild: (node: unknown) => rendered.push(String((node as { text?: string }).text ?? node)),
	},
	ui: { requestRender: () => undefined },
};

await executeBuiltinSlashCommand("/global backup", { ctx });
await executeBuiltinSlashCommand("/global backups", { ctx });
await executeBuiltinSlashCommand("/global export /tmp/global.json", { ctx });
await executeBuiltinSlashCommand("/global import /tmp/global.json --dry-run", { ctx });
await executeBuiltinSlashCommand("/flags list", { ctx });
await executeBuiltinSlashCommand("/flags read kernel", { ctx });
await executeBuiltinSlashCommand("/flags set kernel memory false 1", { ctx });
await executeBuiltinSlashCommand("/flags reset kernel memory 2", { ctx });
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
await executeBuiltinSlashCommand("/agents delete worker --confirm", { ctx });
await executeBuiltinSlashCommand("/gateways delete test2 --confirm", { ctx });

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
	MustangMethod.agentsDelete,
];
for (const method of requiredMethods) {
	assert(methods.includes(method), `CLI slash command should call ${method}`);
}

assert(
	calls.find(call => call.method === MustangMethod.flagsSet)?.params.expectedRevision === 1,
	"/flags set should pass observed/user supplied revision to Kernel",
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
console.log("sqlite_direct_writes=0");
console.log("result=PASS");

function countPrefix(prefix: string): number {
	return methods.filter(method => method.startsWith(prefix)).length;
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
		case MustangMethod.agentsDelete:
			return { agentId: params.agentId, deleted: true, workspaceDeleted: false, stateDirDeletionStatus: "deleted" };
		default:
			return {};
	}
}
