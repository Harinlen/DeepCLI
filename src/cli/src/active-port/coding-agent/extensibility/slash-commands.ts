// @ts-nocheck
import type { SlashCommand } from "@/tui/index.js";
import { getAvailableThemesSync } from "../modes/theme/theme";

type Item = { value: string; label?: string; description?: string };

const SESSION_ACTIONS: Item[] = [
	{ value: "info", label: "info", description: "Show session info and stats" },
	{ value: "current", label: "current", description: "Show current session" },
	{ value: "list", label: "list", description: "List recent sessions" },
	{ value: "new", label: "new", description: "Create and switch to a new session" },
	{ value: "load", label: "load", description: "Load a session by id" },
	{ value: "resume", label: "resume", description: "Resume a session by id without replay" },
	{ value: "switch", label: "switch", description: "Switch by list number or id" },
	{ value: "rename", label: "rename", description: "Rename current session" },
	{ value: "archive", label: "archive", description: "Archive current session" },
	{ value: "unarchive", label: "unarchive", description: "Unarchive current session" },
	{ value: "delete", label: "delete", description: "Delete current session and return to selector" },
];

const MODEL_ACTIONS: Item[] = [
	{ value: "list", label: "list", description: "Open model selector" },
	{ value: "add", label: "add", description: "Add a model" },
	{ value: "current", label: "current", description: "Show current-used roles" },
	{ value: "use", label: "use", description: "Set current-used role" },
];

const WEBFETCH_ACTIONS: Item[] = [
	{ value: "backend", label: "backend", description: "Choose WebFetch backend" },
	{ value: "browser", label: "browser", description: "Install or inspect WebBridge" },
	{ value: "config", label: "config", description: "Show or set backend config" },
	{ value: "install", label: "install", description: "Install backend dependencies" },
];

const WEBFETCH_BACKENDS: Item[] = [
	{ value: "auto", label: "auto", description: "Use fallback order" },
	{ value: "httpx", label: "httpx", description: "Direct HTTP fetch" },
	{ value: "crawl4ai", label: "crawl4ai", description: "Local browser rendering" },
	{ value: "firecrawl", label: "firecrawl", description: "External service" },
	{ value: "parallel", label: "parallel", description: "External service" },
	{ value: "exa", label: "exa", description: "External service" },
	{ value: "tavily", label: "tavily", description: "External service" },
	{ value: "browser", label: "browser", description: "Paired Chrome browser" },
];

const WEBBRIDGE_ACTIONS: Item[] = [
	{ value: "install", label: "install", description: "Open guided installer" },
	{ value: "status", label: "status", description: "Show bridge status" },
	{ value: "pair", label: "pair", description: "Generate pairing token" },
	{ value: "reset", label: "reset", description: "Reset pairing" },
];

const THEME_ACTIONS: Item[] = [
	{ value: "current", label: "current", description: "Show current theme" },
	{ value: "list", label: "list", description: "List available themes" },
	{ value: "set", label: "set", description: "Set theme" },
];

const KERNEL_ACTIONS: Item[] = [
	{ value: "status", label: "status", description: "Show runtime status" },
	{ value: "restart", label: "restart", description: "Restart the supervised runtime" },
];

const GLOBAL_ACTIONS: Item[] = [
	{ value: "backup", label: "backup", description: "Create a ResourceStore backup" },
	{ value: "backups", label: "backups", description: "List ResourceStore backups" },
	{ value: "export", label: "export", description: "Export ResourceStore resources" },
	{ value: "import", label: "import", description: "Dry-run a ResourceStore import" },
	{ value: "restore", label: "restore", description: "Restore a ResourceStore backup" },
];

const FLAG_ACTIONS: Item[] = [
	{ value: "read", label: "read", description: "Read one flag value" },
	{ value: "set", label: "set", description: "Stage a flag value for restart" },
	{ value: "reset", label: "reset", description: "Reset a staged flag value" },
	{ value: "list", label: "list", description: "Open the flag editor or list flags" },
];

const SECRETS_ACTIONS: Item[] = [
	{ value: "list", label: "list", description: "List secret metadata" },
	{ value: "audit", label: "audit", description: "Show secret audit events" },
	{ value: "rename", label: "rename", description: "Rename a secret metadata label" },
	{ value: "delete", label: "delete", description: "Delete a secret with confirmation" },
];

const AGENT_ACTIONS: Item[] = [
	{ value: "list", label: "list", description: "List durable agents" },
	{ value: "read", label: "read", description: "Read one durable agent" },
	{ value: "create", label: "create", description: "Create a durable agent" },
	{ value: "add", label: "add", description: "Create a durable agent" },
	{ value: "set-identity", label: "set-identity", description: "Update an agent identity" },
	{ value: "use", label: "use", description: "Route this CLI session to an agent" },
	{ value: "current", label: "current", description: "Show this CLI session's agent target" },
	{ value: "clear-use", label: "clear-use", description: "Restore this CLI session to main" },
	{ value: "send", label: "send", description: "Send a message through Access Router" },
	{ value: "delete", label: "delete", description: "Delete a durable agent" },
	{ value: "bindings", label: "bindings", description: "List agent bindings" },
	{ value: "bind", label: "bind", description: "Bind an agent to a gateway channel" },
	{ value: "unbind", label: "unbind", description: "Remove an agent gateway binding" },
	{ value: "start", label: "start", description: "Start an agent runtime" },
	{ value: "stop", label: "stop", description: "Stop an agent runtime" },
	{ value: "restart", label: "restart", description: "Restart an agent runtime" },
	{ value: "health", label: "health", description: "Show agent health" },
	{ value: "grants", label: "grants", description: "List management grants" },
	{ value: "grant", label: "grant", description: "Grant a capability" },
	{ value: "revoke-grant", label: "revoke-grant", description: "Revoke a grant" },
];

const GATEWAYS_ACTIONS: Item[] = [
	{ value: "list", label: "list", description: "List gateways" },
	{ value: "read", label: "read", description: "Read gateway status" },
	{ value: "create", label: "create", description: "Unavailable until Kernel exposes gateway creation" },
	{ value: "delete", label: "delete", description: "Unavailable until Kernel exposes gateway deletion" },
	{ value: "enable", label: "enable", description: "Enable a gateway" },
	{ value: "disable", label: "disable", description: "Disable a gateway" },
	{ value: "reload", label: "reload", description: "Reload a gateway" },
	{ value: "bindings", label: "bindings", description: "List gateway bindings" },
	{ value: "bind", label: "bind", description: "Bind a gateway channel to an agent" },
	{ value: "unbind", label: "unbind", description: "Remove a gateway binding" },
];

const MCP_ACTIONS: Item[] = [
	{ value: "list", label: "list", description: "List MCP server declarations" },
	{ value: "read", label: "read", description: "Read one MCP server declaration" },
	{ value: "create", label: "create", description: "Create an MCP server declaration" },
	{ value: "update", label: "update", description: "Update an MCP server declaration" },
	{ value: "delete", label: "delete", description: "Delete an MCP server declaration" },
];

const SKILLS_ACTIONS: Item[] = [
	{ value: "list", label: "list", description: "List visible skills" },
	{ value: "inspect", label: "inspect", description: "Inspect one skill manifest" },
	{ value: "search", label: "search", description: "Search installable skill sources" },
	{ value: "sources", label: "sources", description: "List known skill sources" },
	{ value: "install", label: "install", description: "Install or import a skill" },
	{ value: "refresh", label: "refresh", description: "Refresh skill discovery" },
	{ value: "check", label: "check", description: "Check installed skill provenance" },
	{ value: "update", label: "update", description: "Update installed skills" },
	{ value: "audit", label: "audit", description: "Audit installed skills" },
	{ value: "uninstall", label: "uninstall", description: "Archive an installed skill" },
];

const CRON_ACTIONS: Item[] = [
	{ value: "list", label: "list", description: "List cron jobs" },
	{ value: "create", label: "create", description: "Create a cron job" },
	{ value: "delete", label: "delete", description: "Delete a cron job" },
];

const MEMORY_ACTIONS: Item[] = [
	{ value: "list", label: "list", description: "List memories" },
	{ value: "show", label: "show", description: "Show one memory" },
	{ value: "delete", label: "delete", description: "Delete one memory" },
];

export const BUILTIN_SLASH_COMMANDS: SlashCommand[] = [
	{ name: "clear", description: "Clear the current conversation view" },
	{ name: "compact", description: "Compact conversation context" },
	{ name: "cost", description: "Show usage and cost" },
	{ name: "cron", description: "Manage scheduled cron jobs", getArgumentCompletions: completeCronArguments },
	{ name: "exit", description: "Exit DeepCLI" },
	{ name: "agent", description: "Manage durable agents", getArgumentCompletions: completeAgentArguments },
	{ name: "help", description: "Show available commands" },
	{ name: "flag", description: "Read or stage startup flags", getArgumentCompletions: completeFlagArguments },
	{ name: "gateways", description: "Manage Access Router gateways", getArgumentCompletions: completeGatewaysArguments },
	{ name: "global", description: "Backup, export, or dry-run import global ResourceStore data", getArgumentCompletions: completeGlobalArguments },
	{ name: "memory", description: "List, show, or delete memories", getArgumentCompletions: completeMemoryArguments },
	{ name: "kernel", description: "Inspect or restart the local runtime", getArgumentCompletions: completeKernelArguments },
	{ name: "mcp", description: "Manage MCP server declarations", getArgumentCompletions: completeMcpArguments },
	{ name: "model", description: "Manage models", getArgumentCompletions: completeModelArguments },
	{ name: "plan", description: "Enter, exit, or inspect plan mode", getArgumentCompletions: completePlanArguments },
	{ name: "quit", description: "Exit DeepCLI" },
	{ name: "secrets", description: "Manage secret metadata", getArgumentCompletions: completeSecretsArguments },
	{ name: "session", description: "List, resume, or delete sessions", getArgumentCompletions: completeSessionArguments },
	{ name: "skills", description: "Manage skills and skill-installed commands", getArgumentCompletions: completeSkillsArguments },
	{ name: "theme", description: "Show or switch theme", getArgumentCompletions: completeThemeArguments },
	{ name: "webfetch", description: "Manage WebFetch backend", getArgumentCompletions: completeWebFetchArguments },
];

export async function loadSlashCommands(): Promise<SlashCommand[]> {
	return [];
}

function completeSessionArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = "", value = ""] = argumentPrefix.split(/\s+/, 2);
	if (argumentPrefix.includes(" ") && subcommand === "delete") {
		return filterCompletions(value, [{ value: "confirm", label: "confirm", description: "Permanently delete current session" }]);
	}
	if (argumentPrefix.includes(" ") && (subcommand === "switch" || subcommand === "load" || subcommand === "resume")) return null;
	return filterCompletions(subcommand, SESSION_ACTIONS);
}

function completeModelArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = ""] = argumentPrefix.split(/\s+/, 1);
	if (argumentPrefix.includes(" ")) return null;
	return filterCompletions(subcommand, MODEL_ACTIONS);
}

function completeWebFetchArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = "", value = ""] = argumentPrefix.split(/\s+/, 2);
	if (argumentPrefix.includes(" ") && (subcommand === "backend" || subcommand === "install")) {
		return filterCompletions(value, WEBFETCH_BACKENDS);
	}
	if (argumentPrefix.includes(" ") && subcommand === "browser") {
		return filterCompletions(value, WEBBRIDGE_ACTIONS);
	}
	if (argumentPrefix.includes(" ")) return null;
	return filterCompletions(subcommand, WEBFETCH_ACTIONS);
}

function completePlanArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = ""] = argumentPrefix.split(/\s+/, 1);
	return filterCompletions(subcommand, [
		{ value: "enter", label: "enter", description: "Enter plan mode" },
		{ value: "exit", label: "exit", description: "Exit plan mode" },
		{ value: "status", label: "status", description: "Show plan mode status" },
	]);
}

function completeThemeArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = "", value = ""] = argumentPrefix.split(/\s+/, 2);
	if (argumentPrefix.includes(" ") && subcommand === "set") {
		return filterCompletions(value, getAvailableThemesSync().map(name => ({ value: name, label: name })));
	}
	if (argumentPrefix.includes(" ")) return null;
	return filterCompletions(subcommand, THEME_ACTIONS);
}

function completeKernelArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = ""] = argumentPrefix.split(/\s+/, 1);
	if (argumentPrefix.includes(" ")) return null;
	return filterCompletions(subcommand, KERNEL_ACTIONS);
}

function completeGlobalArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = ""] = argumentPrefix.split(/\s+/, 1);
	if (argumentPrefix.includes(" ")) return null;
	return filterCompletions(subcommand, GLOBAL_ACTIONS);
}

function completeFlagArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = ""] = argumentPrefix.split(/\s+/, 1);
	if (argumentPrefix.includes(" ")) return null;
	return filterCompletions(subcommand, FLAG_ACTIONS);
}

function completeSecretsArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = ""] = argumentPrefix.split(/\s+/, 1);
	if (argumentPrefix.includes(" ")) return null;
	return filterCompletions(subcommand, SECRETS_ACTIONS);
}

function completeAgentArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = ""] = argumentPrefix.split(/\s+/, 1);
	if (argumentPrefix.includes(" ")) return null;
	return filterCompletions(subcommand, AGENT_ACTIONS);
}

function completeGatewaysArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = ""] = argumentPrefix.split(/\s+/, 1);
	if (argumentPrefix.includes(" ")) return null;
	return filterCompletions(subcommand, GATEWAYS_ACTIONS);
}

function completeMcpArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = ""] = argumentPrefix.split(/\s+/, 1);
	if (argumentPrefix.includes(" ")) return null;
	return filterCompletions(subcommand, MCP_ACTIONS);
}

function completeSkillsArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = ""] = argumentPrefix.split(/\s+/, 1);
	if (argumentPrefix.includes(" ")) return null;
	return filterCompletions(subcommand, SKILLS_ACTIONS);
}

function completeCronArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = ""] = argumentPrefix.split(/\s+/, 1);
	if (argumentPrefix.includes(" ")) return null;
	return filterCompletions(subcommand, CRON_ACTIONS);
}

function completeMemoryArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = ""] = argumentPrefix.split(/\s+/, 1);
	if (argumentPrefix.includes(" ")) return null;
	return filterCompletions(subcommand, MEMORY_ACTIONS);
}

function filterCompletions(prefix: string, items: Item[]): Item[] | null {
	const normalized = prefix.toLowerCase();
	const filtered = items.filter(item => item.value.toLowerCase().startsWith(normalized));
	return filtered.length > 0 ? filtered : null;
}
