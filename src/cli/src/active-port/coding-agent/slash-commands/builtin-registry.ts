// @ts-nocheck
import {
	getAvailableThemes,
	getCurrentThemeName,
	setTheme,
	theme,
} from "../modes/theme/theme";
import { Text } from "@/tui/index.js";
import { formatWebFetchSetupFailure } from "@/webfetch/diagnostics.js";
import { MustangMethod } from "@/acp/methods.js";

export interface ParsedBuiltinSlashCommand {
	name: string;
	args?: string;
}

export interface BuiltinSlashCommandRuntime {
	[key: string]: unknown;
}

export async function executeBuiltinSlashCommand(
	command: ParsedBuiltinSlashCommand | string,
	runtime?: BuiltinSlashCommandRuntime,
): Promise<boolean | string | undefined> {
	const parsed = typeof command === "string" ? parseBuiltinSlashCommand(command) : command;
	const ctx = runtime?.ctx as any;
	if (!parsed || !ctx) return undefined;

	try {
		switch (parsed.name) {
			case "compact":
				await ctx.handleCompactCommand?.(parsed.args ?? "");
				return true;
			case "cost":
				await ctx.handleUsageCommand?.();
				return true;
			case "memory":
				return await executeMemoryCommand(ctx, parsed.args ?? "");
			case "cron":
				return await executeCronCommand(ctx, parsed.args ?? "");
			case "kernel":
				return await executeKernelCommand(ctx, parsed.args ?? "");
			case "plan":
				return await executePlanCommand(ctx, parsed.args ?? "");
			case "session":
				return await executeSessionCommand(ctx, parsed.args ?? "");
			case "model":
				return await executeModelCommand(ctx, parsed.args ?? "");
			case "webfetch":
				return await executeWebFetchCommand(ctx, parsed.args ?? "");
			case "global":
				return await executeGlobalCommand(ctx, parsed.args ?? "");
			case "flag":
				return await executeFlagCommand(ctx, parsed.args ?? "");
			case "secrets":
				return await executeSecretsCommand(ctx, parsed.args ?? "");
			case "agents":
				return await executeAgentsCommand(ctx, parsed.args ?? "");
			case "agent":
				return await executeAgentCommand(ctx, parsed.args ?? "");
			case "gateways":
				return await executeGatewaysCommand(ctx, parsed.args ?? "");
			case "mcp":
				return await executeMcpCommand(ctx, parsed.args ?? "");
			case "skills":
				return await executeSkillsCommand(ctx, parsed.args ?? "");
			case "theme":
				return await executeThemeCommand(ctx, parsed.args ?? "");
			case "clear":
				await ctx.handleClearCommand?.();
				return true;
			case "help":
				await ctx.handleHotkeysCommand?.();
				return true;
			case "quit":
			case "exit":
				await ctx.shutdown?.();
				return true;
			default:
				return undefined;
		}
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		ctx.showError?.(`/${parsed.name} failed: ${message}`);
		return true;
	}
}

async function executeSkillsCommand(ctx: any, argsText: string): Promise<boolean | string> {
	const args = splitArgs(argsText);
	const subcommand = args[0] ?? "list";
	if (subcommand === "list") {
		renderManagementResult(ctx, "Skills", await requestManagement(ctx, MustangMethod.skillsList, {}));
		return true;
	}
	if (subcommand === "inspect") {
		if (!args[1]) {
			ctx.showWarning?.("Usage: /skills inspect <name>");
			return true;
		}
		renderManagementResult(ctx, "Skill", await requestManagement(ctx, MustangMethod.skillsInspect, { name: args[1] }));
		return true;
	}
	if (subcommand === "refresh") {
		renderManagementResult(ctx, "Skills refreshed", await requestManagement(ctx, MustangMethod.skillsRefresh, {}));
		return true;
	}
	if (["install", "search", "sources", "check", "update", "audit", "uninstall"].includes(subcommand)) {
		return `/skill:skill-installer ${argsText}`.trim();
	}
	ctx.showWarning?.("Usage: /skills [list|inspect|search|sources|install|refresh|check|update|audit|uninstall]");
	return true;
}

async function executeCronCommand(ctx: any, argsText: string): Promise<boolean> {
	const args = splitArgs(argsText);
	const subcommand = args[0] ?? "list";
	if (subcommand === "list") {
		renderManagementResult(ctx, "Cron jobs", await requestManagement(ctx, MustangMethod.cronList, { includeCompleted: args.includes("--all") }));
		return true;
	}
	if (subcommand === "create") {
		if (!args[1] || args.length < 3) {
			ctx.showWarning?.("Usage: /cron create <schedule> <prompt>");
			return true;
		}
		renderManagementResult(ctx, "Cron job created", await requestManagement(ctx, MustangMethod.cronCreate, {
			schedule: args[1],
			prompt: args.slice(2).join(" "),
		}));
		return true;
	}
	if (subcommand === "delete") {
		if (!args[1]) {
			ctx.showWarning?.("Usage: /cron delete <job-id>");
			return true;
		}
		renderManagementResult(ctx, "Cron job deleted", await requestManagement(ctx, MustangMethod.cronDelete, { id: args[1] }));
		return true;
	}
	ctx.showWarning?.("Usage: /cron [list|create|delete]");
	return true;
}

async function executeMemoryCommand(ctx: any, argsText: string): Promise<boolean> {
	const args = splitArgs(argsText);
	const subcommand = args[0] ?? "list";
	if (subcommand === "list") {
		renderManagementResult(ctx, "Memory", await requestManagement(ctx, MustangMethod.memoryList, optionalPathParam("category", args[1])));
		return true;
	}
	if (subcommand === "show") {
		if (!args[1]) {
			ctx.showWarning?.("Usage: /memory show <name>");
			return true;
		}
		renderManagementResult(ctx, "Memory", await requestManagement(ctx, MustangMethod.memoryShow, { name: args[1] }));
		return true;
	}
	if (subcommand === "delete") {
		if (!args[1] || !args.includes("--confirm")) {
			ctx.showWarning?.("Usage: /memory delete <name> --confirm");
			return true;
		}
		renderManagementResult(ctx, "Memory deleted", await requestManagement(ctx, MustangMethod.memoryDelete, { name: args[1], confirm: true }));
		return true;
	}
	ctx.showWarning?.("Usage: /memory [list|show|delete]");
	return true;
}

async function executeGlobalCommand(ctx: any, argsText: string): Promise<boolean> {
	const args = splitArgs(argsText);
	const subcommand = args[0] ?? "backup";
	if (subcommand === "backup") {
		const result = await requestManagement(ctx, MustangMethod.globalBackup, optionalPathParam("outputDir", args[1]));
		renderManagementResult(ctx, "Global backup", result);
		return true;
	}
	if (subcommand === "backups") {
		const result = await requestManagement(ctx, MustangMethod.globalBackups, optionalPathParam("backupDir", args[1]));
		renderManagementResult(ctx, "Global backups", result);
		return true;
	}
	if (subcommand === "export") {
		const outputPath = args[1] && args[1] !== "--dry-run" ? args[1] : undefined;
		const dryRun = args.includes("--dry-run");
		const result = await requestManagement(ctx, MustangMethod.globalExport, { outputPath, dryRun });
		renderManagementResult(ctx, "Global export", result);
		return true;
	}
	if (subcommand === "import") {
		const inputPath = args.find(arg => !arg.startsWith("--") && arg !== "import");
		if (!inputPath) {
			ctx.showWarning?.("Usage: /global import <path> --dry-run");
			return true;
		}
		if (!args.includes("--dry-run")) {
			ctx.showWarning?.("Only /global import <path> --dry-run is available from the CLI.");
			return true;
		}
		const result = await requestManagement(ctx, MustangMethod.globalImport, { inputPath, dryRun: true });
		renderManagementResult(ctx, "Global import dry-run", result);
		return true;
	}
	if (subcommand === "restore") {
		if (!args[1]) {
			ctx.showWarning?.("Usage: /global restore <backup-id-or-path> --confirm");
			return true;
		}
		const result = await requestManagement(ctx, MustangMethod.globalRestore, {
			backupIdOrPath: args[1],
			confirm: args.includes("--confirm"),
		});
		renderManagementResult(ctx, "Global restore", result);
		return true;
	}
	ctx.showWarning?.("Usage: /global [backup|backups|export|import|restore]");
	return true;
}

async function executeFlagCommand(ctx: any, argsText: string): Promise<boolean> {
	const args = splitArgs(argsText);
	if ((!args[0] || args[0] === "list") && ctx.showFlagsSelector) {
		ctx.showFlagsSelector();
		return true;
	}
	const subcommand = args[0] ?? "list";
	if (subcommand === "list") {
		const result = await requestManagement(ctx, MustangMethod.flagsList, {});
		renderFlagsList(ctx, result);
		return true;
	}
	if (subcommand === "read") {
		const path = parseFlagPath(args[1]);
		if (!path) {
			ctx.showWarning?.("Usage: /flag read <section>.<key>");
			return true;
		}
		const section = await requestManagement(ctx, MustangMethod.flagsRead, { section: path.section });
		renderFlagValue(ctx, section, path.key);
		return true;
	}
	if (subcommand === "set") {
		const path = parseFlagPath(args[1]);
		if (!path || args.length < 3) {
			ctx.showWarning?.("Usage: /flag set <section>.<key> <value> [revision]");
			return true;
		}
		const revision = parseOptionalRevision(args[3]);
		const expectedRevision = revision ?? await readConfigRevision(ctx, MustangMethod.flagsRead, path.section);
		const result = await requestManagement(ctx, MustangMethod.flagsSet, {
			section: path.section,
			key: path.key,
			value: parseConfigValue(args[2]),
			...(expectedRevision !== undefined ? { expectedRevision } : {}),
		});
		renderManagementResult(ctx, "Flag staged", result);
		return true;
	}
	if (subcommand === "reset") {
		const path = parseFlagPath(args[1]);
		if (!path) {
			ctx.showWarning?.("Usage: /flag reset <section>.<key> [revision]");
			return true;
		}
		const revision = parseOptionalRevision(args[2]);
		const expectedRevision = revision ?? await readConfigRevision(ctx, MustangMethod.flagsRead, path.section);
		const result = await requestManagement(ctx, MustangMethod.flagsReset, {
			section: path.section,
			key: path.key,
			...(expectedRevision !== undefined ? { expectedRevision } : {}),
		});
		renderManagementResult(ctx, "Flag reset staged", result);
		return true;
	}
	ctx.showWarning?.("Usage: /flag [list|read|set|reset]");
	return true;
}

type FlagSectionView = {
	section?: unknown;
	payload?: unknown;
	revision?: unknown;
	pendingRestart?: unknown;
};

function renderFlagsList(ctx: any, result: unknown): void {
	const sections = Array.isArray((result as { sections?: unknown } | undefined)?.sections)
		? (result as { sections: FlagSectionView[] }).sections
		: [];
	const lines = sections.flatMap(section => formatFlagSectionLines(section));
	renderLines(ctx, "Flags", lines.length > 0 ? lines : [theme.fg("muted", "No flag sections registered.")]);
}

function renderFlagSection(ctx: any, result: unknown): void {
	const section = result as FlagSectionView;
	const name = String(section?.section ?? "section");
	const lines = formatFlagSectionLines(section);
	renderLines(ctx, `Flags: ${name}`, lines.length > 0 ? lines : [theme.fg("muted", "No flags in this section.")]);
}

function renderFlagValue(ctx: any, result: unknown, key: string): void {
	const section = result as FlagSectionView;
	const name = String(section?.section ?? "section");
	const payload = section?.payload && typeof section.payload === "object" && !Array.isArray(section.payload)
		? section.payload as Record<string, unknown>
		: {};
	if (!Object.prototype.hasOwnProperty.call(payload, key)) {
		ctx.showError?.(`Flag not found: ${name}.${key}`);
		return;
	}
	const revision = Number(section?.revision);
	const revisionText = Number.isFinite(revision) ? `rev ${revision}` : "rev ?";
	const restartText = section?.pendingRestart ? theme.fg("warning", "restart pending") : "active";
	const value = payload[key];
	const line = `${flagMarker(value)} ${`${name}.${key}`.padEnd(28)} ${formatFlagValue(value).padEnd(8)} ${theme.fg("muted", revisionText)}  ${restartText}`;
	renderLines(ctx, `Flag: ${name}.${key}`, [line]);
}

function formatFlagSectionLines(section: FlagSectionView): string[] {
	const name = String(section?.section ?? "unknown");
	const payload = section?.payload && typeof section.payload === "object" && !Array.isArray(section.payload)
		? section.payload as Record<string, unknown>
		: {};
	const revision = Number(section?.revision);
	const revisionText = Number.isFinite(revision) ? `rev ${revision}` : "rev ?";
	const restartText = section?.pendingRestart ? theme.fg("warning", "restart pending") : "active";
	const keys = Object.keys(payload).sort();
	if (keys.length === 0) {
		return [`${theme.fg("muted", name.padEnd(14))} ${revisionText}  ${restartText}  ${theme.fg("muted", "(empty)")}`];
	}
	return keys.map(key => {
		const value = payload[key];
		const marker = flagMarker(value);
		const label = `${name}.${key}`;
		const command = `/flag set ${name}.${key} ${nextFlagValue(value)}`;
		return `${marker} ${label.padEnd(28)} ${formatFlagValue(value).padEnd(8)} ${theme.fg("muted", revisionText)}  ${restartText}  ${theme.fg("muted", command)}`;
	});
}

function parseFlagPath(value: string | undefined): { section: string; key: string } | undefined {
	if (!value) return undefined;
	const dot = value.indexOf(".");
	if (dot <= 0 || dot === value.length - 1) return undefined;
	return { section: value.slice(0, dot), key: value.slice(dot + 1) };
}

function flagMarker(value: unknown): string {
	if (value === true) return theme.fg("success", "[x]");
	if (value === false) return theme.fg("muted", "[ ]");
	return theme.fg("muted", "[-]");
}

function nextFlagValue(value: unknown): string {
	if (value === true) return "false";
	if (value === false) return "true";
	return "<value>";
}

function formatFlagValue(value: unknown): string {
	if (typeof value === "string") return value;
	if (typeof value === "number" || typeof value === "boolean") return String(value);
	return JSON.stringify(value);
}

async function executeSecretsCommand(ctx: any, argsText: string): Promise<boolean> {
	const args = splitArgs(argsText);
	const subcommand = args[0] ?? "list";
	if (subcommand === "list") {
		renderManagementResult(ctx, "Secrets", await requestManagement(ctx, MustangMethod.secretsList, {}));
		return true;
	}
	if (subcommand === "audit") {
		renderManagementResult(ctx, "Secret audit", await requestManagement(ctx, MustangMethod.secretsAudit, optionalPathParam("secretId", args[1])));
		return true;
	}
	if (subcommand === "rename") {
		if (!args[1] || !args[2] || !args[3]) {
			ctx.showWarning?.("Usage: /secrets rename <secret-id> <name> <revision>");
			return true;
		}
		renderManagementResult(ctx, "Secret renamed", await requestManagement(ctx, MustangMethod.secretsRename, {
			secretId: args[1],
			name: args[2],
			expectedRevision: Number(args[3]),
		}));
		return true;
	}
	if (subcommand === "delete") {
		if (!args[1] || !args[2] || !args.includes("--confirm")) {
			ctx.showWarning?.("Usage: /secrets delete <secret-id> <revision> --confirm");
			return true;
		}
		renderManagementResult(ctx, "Secret deleted", await requestManagement(ctx, MustangMethod.secretsDelete, {
			secretId: args[1],
			expectedRevision: Number(args[2]),
			confirm: true,
		}));
		return true;
	}
	ctx.showWarning?.("Usage: /secrets [list|audit|rename|delete]");
	return true;
}

async function executeAgentsCommand(ctx: any, argsText: string): Promise<boolean> {
	const args = splitArgs(argsText);
	const subcommand = args[0] ?? "list";
	if (subcommand === "list") {
		renderManagementResult(ctx, "Agents", await requestManagement(ctx, MustangMethod.agentsList, { includeBindings: args.includes("--bindings") }));
		return true;
	}
	if (subcommand === "read") {
		if (!args[1]) {
			ctx.showWarning?.("Usage: /agents read <agent-id>");
			return true;
		}
		const result: any = await requestManagement(ctx, MustangMethod.agentsList, { includeBindings: true });
		result.agents = (result.agents ?? []).filter((agent: any) => agent.agentId === args[1] || agent.agent_id === args[1]);
		renderManagementResult(ctx, "Agent", result);
		return true;
	}
	if (subcommand === "create" || subcommand === "add") {
		if (!args[1] || !args[2]) {
			ctx.showWarning?.("Usage: /agents create <agent-id> <workspace> [name]");
			return true;
		}
		renderManagementResult(ctx, "Agent created", await requestManagement(ctx, MustangMethod.agentsAdd, {
			agentId: args[1],
			workspace: args[2],
			...(args[3] ? { name: args.slice(3).join(" ") } : {}),
		}));
		return true;
	}
	if (subcommand === "set-identity") {
		if (!args[1]) {
			ctx.showWarning?.("Usage: /agents set-identity <agent-id> [name] [json-patch]");
			return true;
		}
		const patch = args.length > 3 ? parseJsonObject(args.slice(3).join(" ")) : undefined;
		renderManagementResult(ctx, "Agent identity", await requestManagement(ctx, MustangMethod.agentsSetIdentity, {
			agentId: args[1],
			...(args[2] ? { name: args[2] } : {}),
			...(patch ? { identityPatch: patch } : {}),
		}));
		return true;
	}
	if (subcommand === "bindings") {
		renderManagementResult(ctx, "Agent bindings", await requestManagement(ctx, MustangMethod.agentsBindings, optionalPathParam("agentId", args[1])));
		return true;
	}
	if (subcommand === "delete") {
		if (!args[1] || !args.includes("--confirm")) {
			ctx.showWarning?.("Usage: /agents delete <agent-id> --confirm");
			return true;
		}
		renderManagementResult(ctx, "Agent deleted", await requestManagement(ctx, MustangMethod.agentsDelete, {
			agentId: args[1],
			confirm: true,
		}));
		return true;
	}
	if (subcommand === "bind") {
		if (!args[1] || !args[2]) {
			ctx.showWarning?.("Usage: /agents bind <agent-id> <gateway:channel> [session-id]");
			return true;
		}
		renderManagementResult(ctx, "Agent binding", await requestManagement(ctx, MustangMethod.agentsBind, {
			agentId: args[1],
			bind: args[2],
			...(args[3] ? { sessionId: args[3] } : {}),
		}));
		return true;
	}
	if (subcommand === "unbind") {
		if (!args[1]) {
			ctx.showWarning?.("Usage: /agents unbind <agent-id> [gateway:channel|--all]");
			return true;
		}
		renderManagementResult(ctx, "Agent unbind", await requestManagement(ctx, MustangMethod.agentsUnbind, {
			agentId: args[1],
			...(args.includes("--all") ? { all: true } : { bind: args[2] }),
		}));
		return true;
	}
	if (["start", "stop", "restart", "health"].includes(subcommand)) {
		if (!args[1]) {
			ctx.showWarning?.(`Usage: /agents ${subcommand} <agent-id>`);
			return true;
		}
		const method = subcommand === "start"
			? MustangMethod.agentsStart
			: subcommand === "stop"
				? MustangMethod.agentsStop
				: subcommand === "restart"
					? MustangMethod.agentsRestart
					: MustangMethod.agentsHealth;
		renderManagementResult(ctx, `Agent ${subcommand}`, await requestManagement(ctx, method, { agentId: args[1] }));
		return true;
	}
	if (subcommand === "grants") {
		renderManagementResult(ctx, "Agent grants", await requestManagement(ctx, MustangMethod.agentsGrants, optionalPathParam("agentId", args[1])));
		return true;
	}
	if (subcommand === "grant") {
		if (!args[1] || !args[2]) {
			ctx.showWarning?.("Usage: /agents grant <agent-id> <capability> [scope] [resource]");
			return true;
		}
		renderManagementResult(ctx, "Agent grant", await requestManagement(ctx, MustangMethod.agentsGrant, {
			agentId: args[1],
			capability: args[2],
			...(args[3] ? { scope: args[3] } : {}),
			...(args[4] ? { resource: args[4] } : {}),
		}));
		return true;
	}
	if (subcommand === "revoke-grant") {
		if (!args[1]) {
			ctx.showWarning?.("Usage: /agents revoke-grant <grant-id>");
			return true;
		}
		renderManagementResult(ctx, "Agent grant revoked", await requestManagement(ctx, MustangMethod.agentsRevokeGrant, { grantId: args[1] }));
		return true;
	}
	ctx.showWarning?.("Usage: /agents [list|read|create|add|set-identity|delete|bindings|bind|unbind|start|stop|restart|health|grants|grant|revoke-grant]");
	return true;
}

async function executeAgentCommand(ctx: any, argsText: string): Promise<boolean> {
	const args = splitArgs(argsText);
	if ((args[0] ?? "") !== "send" || !args[1] || args.length < 3) {
		ctx.showWarning?.("Usage: /agent send <agent-id> <message>");
		return true;
	}
	renderManagementResult(ctx, "Agent send", await requestManagement(ctx, MustangMethod.agentSend, {
		agentId: args[1],
		message: args.slice(2).join(" "),
	}));
	return true;
}

async function executeGatewaysCommand(ctx: any, argsText: string): Promise<boolean> {
	const args = splitArgs(argsText);
	const subcommand = args[0] ?? "list";
	if (subcommand === "list") {
		renderManagementResult(ctx, "Gateways", await requestManagement(ctx, MustangMethod.gatewaysList, {}));
		return true;
	}
	if (subcommand === "read" || subcommand === "status") {
		renderManagementResult(ctx, "Gateway status", await requestManagement(ctx, MustangMethod.gatewaysStatus, optionalPathParam("gatewayId", args[1])));
		return true;
	}
	if (subcommand === "create") {
		if (!args[1]) {
			ctx.showWarning?.("Usage: /gateways create <gateway-id> [type] [json-config]");
			return true;
		}
		renderManagementResult(ctx, "Gateway created", await requestManagement(ctx, MustangMethod.gatewaysCreate, {
			gatewayId: args[1],
			gatewayType: args[2] ?? "test",
			config: parseJsonObject(args.slice(3).join(" ")),
			enabled: true,
		}));
		return true;
	}
	if (subcommand === "delete") {
		if (!args[1] || !args.includes("--confirm")) {
			ctx.showWarning?.("Usage: /gateways delete <gateway-id> --confirm");
			return true;
		}
		renderManagementResult(ctx, "Gateway deleted", await requestManagement(ctx, MustangMethod.gatewaysDelete, {
			gatewayId: args[1],
			confirm: true,
		}));
		return true;
	}
	if (subcommand === "enable" || subcommand === "disable") {
		if (!args[1]) {
			ctx.showWarning?.(`Usage: /gateways ${subcommand} <gateway-id>`);
			return true;
		}
		renderManagementResult(ctx, `Gateway ${subcommand}d`, await requestManagement(ctx, subcommand === "enable" ? MustangMethod.gatewaysEnable : MustangMethod.gatewaysDisable, {
			gatewayId: args[1],
		}));
		return true;
	}
	if (subcommand === "reload") {
		if (!args[1]) {
			ctx.showWarning?.("Usage: /gateways reload <gateway-id> [--fail]");
			return true;
		}
		renderManagementResult(ctx, "Gateway reloaded", await requestManagement(ctx, MustangMethod.gatewaysReload, {
			gatewayId: args[1],
			fail: args.includes("--fail"),
		}));
		return true;
	}
	if (subcommand === "bindings") {
		renderManagementResult(ctx, "Gateway bindings", await requestManagement(ctx, MustangMethod.gatewaysBindings, {
			...(args[1] ? { gatewayId: args[1] } : {}),
		}));
		return true;
	}
	if (subcommand === "bind") {
		if (!args[1] || !args[2] || !args[3]) {
			ctx.showWarning?.("Usage: /gateways bind <gateway-id> <channel-key> <agent-id> [session-id]");
			return true;
		}
		renderManagementResult(ctx, "Gateway binding", await requestManagement(ctx, MustangMethod.gatewaysBind, {
			gatewayId: args[1],
			channelKey: args[2],
			agentId: args[3],
			...(args[4] ? { sessionId: args[4] } : {}),
		}));
		return true;
	}
	if (subcommand === "unbind") {
		if (!args[1]) {
			ctx.showWarning?.("Usage: /gateways unbind <binding-id>");
			return true;
		}
		renderManagementResult(ctx, "Gateway unbound", await requestManagement(ctx, MustangMethod.gatewaysUnbind, {
			bindingId: args[1],
		}));
		return true;
	}
	ctx.showWarning?.("Usage: /gateways [list|read|status|create|delete|enable|disable|reload|bindings|bind|unbind]");
	return true;
}

async function executeMcpCommand(ctx: any, argsText: string): Promise<boolean> {
	const args = splitArgs(argsText);
	const subcommand = args[0] ?? "list";
	if (subcommand === "list") {
		renderManagementResult(ctx, "MCP servers", await requestManagement(ctx, MustangMethod.mcpList, {}));
		return true;
	}
	if (subcommand === "read") {
		if (!args[1]) {
			ctx.showWarning?.("Usage: /mcp read <name>");
			return true;
		}
		renderManagementResult(ctx, "MCP server", await requestManagement(ctx, MustangMethod.mcpRead, { name: args[1] }));
		return true;
	}
	if (subcommand === "create" || subcommand === "update") {
		if (!args[1] || args.length < 3) {
			ctx.showWarning?.(`Usage: /mcp ${subcommand} <name> <json-config> [revision]`);
			return true;
		}
		const revision = parseOptionalRevision(args[args.length - 1]);
		const jsonArgs = revision === undefined ? args.slice(2) : args.slice(2, -1);
		const method = subcommand === "create" ? MustangMethod.mcpCreate : MustangMethod.mcpUpdate;
		const result = await requestManagement(ctx, method, {
			name: args[1],
			config: parseJsonObject(jsonArgs.join(" ")),
			...(revision !== undefined ? { expectedRevision: revision } : {}),
		});
		renderManagementResult(ctx, subcommand === "create" ? "MCP server created" : "MCP server updated", result);
		return true;
	}
	if (subcommand === "delete") {
		if (!args[1]) {
			ctx.showWarning?.("Usage: /mcp delete <name> [revision]");
			return true;
		}
		const revision = parseOptionalRevision(args[2]);
		renderManagementResult(ctx, "MCP server deleted", await requestManagement(ctx, MustangMethod.mcpDelete, {
			name: args[1],
			...(revision !== undefined ? { expectedRevision: revision } : {}),
		}));
		return true;
	}
	ctx.showWarning?.("Usage: /mcp [list|read|create|update|delete]");
	return true;
}

async function executePlanCommand(ctx: any, argsText: string): Promise<boolean | string> {
	const args = splitArgs(argsText);
	const subcommand = args[0] ?? "enter";
	const kernelMode = ctx.session?.currentPermissionMode ?? "default";
	if (subcommand === "enter") {
		if (kernelMode === "plan") {
			await ctx.syncPlanModeWithPermissionMode?.("plan");
			ctx.statusLine?.invalidate?.();
			ctx.showStatus?.("Plan mode is already active.");
			return true;
		}
		if (ctx.enterPlanModeCommand) {
			await ctx.enterPlanModeCommand();
		} else {
			await ctx.handlePlanModeCommand?.();
		}
		return true;
	}
	if (subcommand === "exit") {
		if (kernelMode !== "plan") {
			await ctx.syncPlanModeWithPermissionMode?.(kernelMode);
			ctx.showWarning?.("Plan mode is not active.");
			return true;
		}
		await ctx.syncPlanModeWithPermissionMode?.("plan");
		if (ctx.exitPlanModeCommand) {
			await ctx.exitPlanModeCommand();
		} else {
			await ctx.session?.setPermissionMode?.("default");
			await ctx.handlePlanModeCommand?.();
		}
		return true;
	}
	if (subcommand === "status") {
		await ctx.syncPlanModeWithPermissionMode?.(kernelMode);
		ctx.showStatus?.(kernelMode === "plan" ? "Plan mode is active." : "Plan mode is not active.");
		return true;
	}
	if (kernelMode === "plan") {
		await ctx.syncPlanModeWithPermissionMode?.("plan");
		return argsText;
	}
	if (ctx.enterPlanModeCommand) {
		await ctx.enterPlanModeCommand(argsText);
	} else {
		await ctx.handlePlanModeCommand?.(argsText);
	}
	return true;
}

async function executeKernelCommand(ctx: any, argsText: string): Promise<boolean> {
	const args = splitArgs(argsText);
	const subcommand = args[0] ?? "status";
	if (subcommand === "status") {
		const result = await ctx.session?.runtimeStatus?.();
		renderKernelStatus(ctx, result?.status ?? {});
		return true;
	}
	if (subcommand === "restart") {
		const result = await ctx.session?.runtimeRestart?.("CLI /kernel restart");
		ctx.showStatus?.("Kernel runtime restarted.");
		renderKernelStatus(ctx, result?.status ?? {});
		return true;
	}
	ctx.showWarning?.("Usage: /kernel [status|restart]");
	return true;
}

function renderKernelStatus(ctx: any, status: Record<string, unknown>): void {
	const runtimeStatus = String(status.status ?? (status.ready ? "ready" : "unknown"));
	const children = status.children && typeof status.children === "object"
		? Object.entries(status.children as Record<string, any>).map(([name, child]) => {
			const pid = child?.pid ?? "?";
			const running = child?.running === false ? "stopped" : "running";
			return `${theme.fg("muted", name.padEnd(8))} pid=${pid} ${running}`;
		})
		: [];
	ctx.chatContainer?.addChild?.(new Text(theme.fg("accent", "Kernel runtime"), 1, 0));
	ctx.chatContainer?.addChild?.(new Text(`status: ${runtimeStatus}`, 1, 0));
	for (const line of children) {
		ctx.chatContainer?.addChild?.(new Text(line, 1, 0));
	}
	ctx.ui?.requestRender?.();
}

function parseBuiltinSlashCommand(input: string): ParsedBuiltinSlashCommand | undefined {
	const trimmed = input.trim();
	if (!trimmed.startsWith("/")) return undefined;
	const withoutSlash = trimmed.slice(1);
	const spaceIndex = withoutSlash.search(/\s/);
	if (spaceIndex === -1) return { name: withoutSlash };
	return {
		name: withoutSlash.slice(0, spaceIndex),
		args: withoutSlash.slice(spaceIndex + 1).trim(),
	};
}

async function executeSessionCommand(ctx: any, argsText: string): Promise<boolean> {
	const args = splitArgs(argsText);
	const subcommand = args[0] ?? "info";
	const session = ctx.session;

	switch (subcommand) {
		case "info":
		case "current":
			await ctx.handleSessionCommand?.();
			return true;
		case "list": {
			await ctx.showSessionSelector?.();
			return true;
		}
		case "new": {
			const id = await session.createSession?.();
			await ctx.refreshWelcomeRecentSessions?.();
			ctx.showStatus?.(`Created session ${id}`);
			ctx.updateEditorTopBorder?.();
			return true;
		}
		case "switch":
		case "load":
		case "resume": {
			const target = await resolveSessionTarget(ctx, args[1]);
			if (!target) {
				ctx.showWarning?.(`Usage: /session ${subcommand} <session-id>`);
				return true;
			}
			const id = await session.loadSession?.(target);
			await ctx.refreshWelcomeRecentSessions?.();
			ctx.showStatus?.(`Loaded session ${id}`);
			ctx.updateEditorTopBorder?.();
			return true;
		}
		case "rename": {
			const title = args.slice(1).join(" ").trim();
			if (!title) {
				ctx.showWarning?.("Usage: /session rename <title>");
				return true;
			}
			await session.sessionManager?.setSessionName?.(title, "user");
			ctx.updateEditorBorderColor?.();
			ctx.showStatus?.(`Session renamed to "${title}".`);
			return true;
		}
		case "archive":
		case "unarchive": {
			await session.archiveCurrentSession?.(subcommand === "archive");
			await ctx.refreshWelcomeRecentSessions?.();
			ctx.showStatus?.(subcommand === "archive" ? "Archived current session" : "Unarchived current session");
			ctx.updateEditorTopBorder?.();
			return true;
		}
		case "delete": {
			if (args[1] !== "confirm") {
				ctx.showWarning?.("Run /session delete confirm to permanently delete the current session");
				return true;
			}
			const id = await session.deleteCurrentSessionAndCreate?.();
			await ctx.refreshWelcomeRecentSessions?.();
			ctx.showStatus?.(`Deleted session and switched to ${id}`);
			ctx.updateEditorTopBorder?.();
			return true;
		}
		default:
			ctx.showWarning?.("Usage: /session [list|switch|new|load|current|info|rename|archive|unarchive|delete]");
			return true;
	}
}

async function resolveSessionTarget(ctx: any, rawTarget: string | undefined): Promise<string | undefined> {
	if (!rawTarget) return undefined;
	const numeric = Number(rawTarget);
	if (Number.isInteger(numeric) && numeric >= 1) {
		const sessions = await ctx.session?.listSessions?.(50);
		const session = sessions?.[numeric - 1];
		if (session?.sessionId) return session.sessionId;
	}
	return rawTarget;
}

async function executeModelCommand(ctx: any, argsText: string): Promise<boolean> {
	const args = splitArgs(argsText);
	const subcommand = args[0] ?? await defaultModelSubcommand(ctx);
	if (subcommand === "list") {
		const state = await ctx.session?.listProviderModels?.().catch(() => undefined);
		if (!state?.models?.length) {
			ctx.showWarning?.("No models available. Use /model add to add a model.");
			return true;
		}
		ctx.showModelSelector?.();
		return true;
	}
	if (subcommand === "add") {
		ctx.showModelAdd?.();
		return true;
	}
	if (subcommand === "current") {
		const state = await ctx.session.listProviderModels?.();
		renderModelCurrent(ctx, state?.currentUsed ?? {});
		return true;
	}
	if (subcommand === "use") {
		const first = args[1];
		const second = args[2];
		if (!first) {
			ctx.showWarning?.("Usage: /model use [role] <provider>/<model>");
			return true;
		}
		const role = second ? first : "default";
		const refText = second ?? first;
		const ref = parseModelRef(refText);
		if (!ref) {
			ctx.showWarning?.("Model must be written as <provider>/<model>");
			return true;
		}
		await ctx.session.setCurrentModelRole?.(role, ref.provider, ref.model);
		ctx.statusLine?.invalidate?.();
		ctx.updateEditorTopBorder?.();
		ctx.showStatus?.(`current_used.${role}: ${ref.provider}/${ref.model}`);
		return true;
	}
	ctx.showWarning?.("Usage: /model [list|add|current|use]");
	return true;
}

async function defaultModelSubcommand(ctx: any): Promise<"list" | "add"> {
	const state = await ctx.session?.listProviderModels?.().catch(() => undefined);
	return state?.models?.length ? "list" : "add";
}

async function executeWebFetchCommand(ctx: any, argsText: string): Promise<boolean> {
	const args = splitArgs(argsText);
	const subcommand = args[0] ?? "backend";
	if (subcommand === "browser") {
		return await executeWebBridgeCommand(ctx, args[1] ?? "install");
	}
	if (subcommand === "backend") {
		const backend = args[1];
		if (!backend) {
			ctx.showWebFetchBackendSelector?.();
			return true;
		}
		const result = await ctx.session?.setWebFetchBackend?.(backend, false);
		if (result?.credentialRequired && result.credentialRequest) {
			if (result.message) ctx.showError?.(result.message);
			const key = await ctx.showHookInput?.(
				result.message
					? `Enter replacement ${result.credentialRequest.label ?? backend + " API key"}`
					: (result.credentialRequest.prompt ?? `Enter ${backend} API key`),
				result.credentialRequest.envKey ?? "API key",
			);
			if (!key?.trim()) {
				ctx.showWarning?.(`WebFetch backend ${backend} was not changed.`);
				return true;
			}
			const validated = await ctx.session?.setWebFetchBackend?.(backend, false, key.trim());
			if (validated?.credentialRequired) {
				ctx.showError?.(validated.message ?? `Failed to validate ${backend} API key`);
				return true;
			}
			if (validated?.message) ctx.showStatus?.(validated.message);
			else ctx.showStatus?.(`WebFetch backend set to ${backend}`);
			return true;
		}
		if (result?.setupRequired && result.setupPlan) {
			const commands = (result.setupPlan.commands ?? []).join("\n");
			const confirmed = await ctx.showHookConfirm?.(
				`Install ${backend}`,
				[`WebFetch backend "${backend}" needs local dependencies.`, commands].filter(Boolean).join("\n\n"),
			);
			if (!confirmed) {
				ctx.showWarning?.(`WebFetch backend ${backend} was not changed.`);
				return true;
			}
			const stopInstallNotice = showWebFetchInstallNotice(ctx, backend);
			let setup: any;
			try {
				setup = await ctx.session?.setWebFetchBackend?.(backend, true);
			} finally {
				stopInstallNotice();
			}
			if (setup?.setupRequired) {
				ctx.showError?.(formatWebFetchSetupFailure(setup.message ?? `Failed to set WebFetch backend ${backend}`, setup.setupResult));
				return true;
			}
			ctx.showStatus?.(setup?.message ?? `WebFetch backend set to ${backend}`);
			return true;
		}
		if (result?.message) ctx.showStatus?.(result.message);
		else ctx.showStatus?.(`WebFetch backend set to ${backend}`);
		return true;
	}
	if (subcommand === "install") {
		const backend = args[1] ?? await promptForWebFetchInstallBackend(ctx);
		if (!backend) {
			ctx.showWarning?.("WebFetch backend install was cancelled.");
			return true;
		}
		const confirmed = await ctx.showHookConfirm?.(
			`Install ${backend}`,
			`Install or repair local dependencies for WebFetch backend "${backend}".`,
		);
		if (!confirmed) {
			ctx.showWarning?.(`WebFetch backend ${backend} install was cancelled.`);
			return true;
		}
		const stopInstallNotice = showWebFetchInstallNotice(ctx, backend);
		let setup: any;
		try {
			setup = await ctx.session?.setWebFetchBackend?.(backend, true);
		} finally {
			stopInstallNotice();
		}
		if (setup?.setupRequired) {
			ctx.showError?.(formatWebFetchSetupFailure(setup.message ?? `Failed to install WebFetch backend ${backend}`, setup.setupResult));
			return true;
		}
		ctx.showStatus?.(setup?.message ?? `WebFetch backend ${backend} installed.`);
		return true;
	}
	if (subcommand === "config") {
		const path = args[1];
		if (!path) {
			ctx.showWebFetchConfigSelector?.();
			return true;
		}
		if (isWebFetchApiKeyPath(path)) {
			const backend = path.split(".")[0];
			const key = await ctx.showHookInput?.(
				`Enter ${backend} API key`,
				`${backend.toUpperCase()}_API_KEY`,
			);
			if (!key?.trim()) {
				ctx.showWarning?.(`WebFetch ${backend} API key was not changed.`);
				return true;
			}
			try {
				const config = await ctx.session?.setWebFetchConfig?.(path, key.trim());
				ctx.showStatus?.(`WebFetch ${backend} API key updated.`);
				renderWebFetchConfig(ctx, config);
			} catch (error) {
				const message = error instanceof Error ? error.message : String(error);
				ctx.showError?.(message);
			}
			return true;
		}
		if (args.length < 3) {
			ctx.showWarning?.("Usage: /webfetch config <backend>.<key> <value>");
			return true;
		}
		const value = parseConfigValue(args.slice(2).join(" "));
		const config = await ctx.session?.setWebFetchConfig?.(path, value);
		ctx.showStatus?.(`web_fetch.${path} = ${String(value)}`);
		renderWebFetchConfig(ctx, config);
		return true;
	}
	ctx.showWarning?.("Usage: /webfetch [backend [name] | browser [install|status|pair|reset] | config [backend.key value]]");
	return true;
}

async function executeWebBridgeCommand(ctx: any, action: string): Promise<boolean> {
	if (action === "install") {
		const status = await ctx.session?.webBridgePairStart?.();
		const url = status?.installUrl;
		if (!url) {
			ctx.showError?.(status?.message ?? "WebBridge install URL is unavailable.");
			return true;
		}
		ctx.openInBrowser?.(url);
		ctx.showStatus?.(`Opened WebBridge installer: ${url}`);
		return true;
	}
	if (action === "pair") {
		const status = await ctx.session?.webBridgePairStart?.();
		renderWebBridgeStatus(ctx, status);
		return true;
	}
	if (action === "reset") {
		const status = await ctx.session?.webBridgePairReset?.();
		renderWebBridgeStatus(ctx, status);
		return true;
	}
	if (action === "status") {
		const status = await ctx.session?.webBridgeStatus?.(true);
		renderWebBridgeStatus(ctx, status);
		return true;
	}
	ctx.showWarning?.("Usage: /webfetch browser [install|status|pair|reset]");
	return true;
}

function renderWebBridgeStatus(ctx: any, status: any): void {
	if (!status) {
		ctx.showError?.("WebBridge status is unavailable.");
		return;
	}
	ctx.chatContainer?.addChild?.(new Text(theme.fg("accent", "WebBridge"), 1, 0));
	ctx.chatContainer?.addChild?.(new Text(`status: ${status.status ?? "unknown"}`, 1, 0));
	ctx.chatContainer?.addChild?.(new Text(`paired: ${Boolean(status.paired)}`, 1, 0));
	ctx.chatContainer?.addChild?.(new Text(`connected: ${Boolean(status.connected)}`, 1, 0));
	if (status.installUrl) ctx.chatContainer?.addChild?.(new Text(`install: ${status.installUrl}`, 1, 0));
	if (status.bridgeWsUrl) ctx.chatContainer?.addChild?.(new Text(`bridge: ${status.bridgeWsUrl}`, 1, 0));
	if (status.pairingToken) ctx.chatContainer?.addChild?.(new Text(`pairing token: ${status.pairingToken}`, 1, 0));
	if (status.unpackedPath) ctx.chatContainer?.addChild?.(new Text(`unpacked: ${status.unpackedPath}`, 1, 0));
	if (status.message) ctx.showStatus?.(status.message);
}

function renderWebFetchConfig(ctx: any, config: any): void {
	const backend = config?.backend ?? "auto";
	ctx.chatContainer?.addChild?.(new Text(theme.fg("accent", "WebFetch"), 1, 0));
	ctx.chatContainer?.addChild?.(new Text(`backend: ${backend}`, 1, 0));
	const entries = Object.entries(config?.backends ?? {});
	for (const [name, value] of entries) {
		const fields = formatWebFetchConfigFields(value);
		if (fields.length === 0) continue;
		ctx.chatContainer?.addChild?.(new Text(`${name}: ${fields.join(", ")}`, 1, 0));
	}
	ctx.ui?.requestRender?.();
}

function isWebFetchApiKeyPath(path: string): boolean {
	return /\.api_?key$/i.test(path.trim());
}

function formatWebFetchConfigFields(value: unknown): string[] {
	if (!value || typeof value !== "object" || Array.isArray(value)) return [];
	const fields: string[] = [];
	for (const [key, raw] of Object.entries(value as Record<string, unknown>)) {
		if (key === "api_key_ref" || key.endsWith("_key_ref")) continue;
		if (key === "api_key") {
			fields.push(`api_key=${raw === "configured" ? "configured" : "missing"}`);
			continue;
		}
		fields.push(`${key}=${String(raw)}`);
	}
	return fields;
}

function parseConfigValue(value: string): unknown {
	const trimmed = value.trim();
	if (trimmed === "true") return true;
	if (trimmed === "false") return false;
	if (/^-?\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed);
	return trimmed;
}

async function requestManagement(ctx: any, method: string, params: Record<string, unknown>): Promise<unknown> {
	if (!ctx.session?.managementRequest) {
		throw new Error("Kernel management request path is not available");
	}
	return await ctx.session.managementRequest(method, params);
}

function renderManagementResult(ctx: any, title: string, result: unknown): void {
	const text = typeof result === "string" ? result : JSON.stringify(result, null, 2);
	renderLines(ctx, title, [text ?? ""]);
}

function renderLines(ctx: any, title: string, lines: string[]): void {
	if (ctx.chatContainer?.addChild) {
		ctx.chatContainer.addChild(new Text(theme.fg("accent", title), 1, 0));
		ctx.chatContainer.addChild(new Text(lines.join("\n"), 1, 0));
		ctx.ui?.requestRender?.();
		return;
	}
	ctx.showStatus?.(`${title}: ${lines.join("\n")}`);
}

function optionalPathParam(key: string, value: string | undefined): Record<string, unknown> {
	return value ? { [key]: value } : {};
}

function parseOptionalRevision(value: string | undefined): number | undefined {
	if (!value) return undefined;
	const revision = Number(value);
	return Number.isFinite(revision) ? revision : undefined;
}

async function readConfigRevision(
	ctx: any,
	method: string,
	section: string,
): Promise<number | undefined> {
	const result = await requestManagement(ctx, method, { section });
	const revision = Number((result as { revision?: unknown } | undefined)?.revision);
	return Number.isFinite(revision) ? revision : undefined;
}

function parseJsonObject(value: string): Record<string, unknown> {
	const trimmed = value.trim();
	if (!trimmed) return {};
	const parsed = JSON.parse(trimmed);
	if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
		throw new Error("Config must be a JSON object");
	}
	return parsed as Record<string, unknown>;
}

function parseModelRef(value: string | undefined): { provider: string; model: string } | undefined {
	if (!value) return undefined;
	const slash = value.indexOf("/");
	if (slash <= 0 || slash === value.length - 1) return undefined;
	return {
		provider: value.slice(0, slash),
		model: value.slice(slash + 1),
	};
}

function renderModelCurrent(ctx: any, currentUsed: Record<string, [string, string]>): void {
	const entries = Object.entries(currentUsed);
	const lines = entries.length > 0
		? entries.map(([role, ref]) => `${theme.fg("muted", role.padEnd(10))} ${ref[0]}/${ref[1]}`)
		: [theme.fg("muted", "No current-used models configured.")];
	ctx.chatContainer?.addChild?.(new Text(theme.fg("accent", "Current models"), 1, 0));
	for (const line of lines) {
		ctx.chatContainer?.addChild?.(new Text(line, 1, 0));
	}
	ctx.ui?.requestRender?.();
}

async function executeThemeCommand(ctx: any, argsText: string): Promise<boolean> {
	const args = splitArgs(argsText);
	const subcommand = args[0] ?? "current";
	if (subcommand === "current") {
		ctx.showStatus?.(`Current theme: ${getCurrentThemeName() ?? "unknown"}`);
		return true;
	}
	if (subcommand === "list") {
		if (ctx.showThemeSelector) {
			ctx.showThemeSelector();
			return true;
		}
		const current = getCurrentThemeName();
		const themes = await getAvailableThemes();
		const lines = themes.map(name => `${name === current ? "*" : " "} ${name}`);
		ctx.showStatus?.(["Available themes:", ...lines].join("\n"));
		return true;
	}
	if (subcommand === "set") {
		const name = args[1];
		if (!name) {
			ctx.showWarning?.("Usage: /theme set <name>");
			return true;
		}
		const result = await setTheme(name, ctx.enableThemeWatcher ?? true);
		ctx.statusLine?.invalidate?.();
		ctx.updateEditorTopBorder?.();
		ctx.updateEditorBorderColor?.();
		ctx.ui?.invalidate?.();
		ctx.ui?.requestRender?.();
		if (!result.success) {
			ctx.showError?.(`Failed to load theme "${name}": ${result.error}`);
			return true;
		}
		ctx.showStatus?.(`Theme set to ${name}`);
		return true;
	}
	ctx.showWarning?.("Usage: /theme [current|list|set]");
	return true;
}

async function promptForWebFetchInstallBackend(ctx: any): Promise<string | undefined> {
	const state = await ctx.session?.listWebFetchBackends?.().catch(() => undefined);
	const options = (state?.options ?? [])
		.filter((option: any) => option.setupRequired || option.setupPlan)
		.map((option: any) => String(option.id))
		.filter(Boolean);
	if (options.length === 0) {
		options.push("crawl4ai");
	}
	if (options.length === 1) {
		return options[0];
	}
	return await ctx.showHookSelector?.("Install WebFetch backend", options);
}

function showWebFetchInstallNotice(ctx: any, backend: string): () => void {
	const message = `Installing WebFetch backend ${backend}...`;
	ctx.showStatus?.(message);
	ctx.setWorkingMessage?.(message);
	ctx.ensureLoadingAnimation?.();
	return () => {
		ctx.setWorkingMessage?.();
		if (ctx.loadingAnimation) {
			ctx.loadingAnimation.stop?.();
			ctx.loadingAnimation = undefined;
			ctx.statusContainer?.clear?.();
		}
	};
}

function splitArgs(value: string): string[] {
	return value.trim() ? value.trim().split(/\s+/) : [];
}
