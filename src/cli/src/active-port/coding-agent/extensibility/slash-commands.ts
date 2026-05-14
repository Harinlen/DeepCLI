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

export const BUILTIN_SLASH_COMMANDS: SlashCommand[] = [
	{ name: "clear", description: "Clear the current conversation view" },
	{ name: "compact", description: "Compact conversation context" },
	{ name: "cost", description: "Show usage and cost" },
	{ name: "exit", description: "Exit DeepCLI" },
	{ name: "help", description: "Show available commands" },
	{ name: "memory", description: "List, show, or delete memories" },
	{ name: "kernel", description: "Inspect or restart the local runtime", getArgumentCompletions: completeKernelArguments },
	{ name: "model", description: "Manage models", getArgumentCompletions: completeModelArguments },
	{ name: "plan", description: "Enter, exit, or inspect plan mode", getArgumentCompletions: completePlanArguments },
	{ name: "quit", description: "Exit DeepCLI" },
	{ name: "session", description: "List, resume, or delete sessions", getArgumentCompletions: completeSessionArguments },
	{ name: "theme", description: "Show or switch theme", getArgumentCompletions: completeThemeArguments },
	{ name: "webfetch", description: "Manage WebFetch backend", getArgumentCompletions: completeWebFetchArguments },
];

export const CLI_ONLY_SLASH_COMMANDS: SlashCommand[] = BUILTIN_SLASH_COMMANDS.filter(command =>
	["clear", "exit", "quit", "theme"].includes(command.name),
);

const LOCAL_ARGUMENT_COMPLETERS = new Map(
	BUILTIN_SLASH_COMMANDS.map(command => [command.name, command.getArgumentCompletions]),
);
LOCAL_ARGUMENT_COMPLETERS.set("thinking", completeThinkingArguments);

export function getLocalArgumentCompleter(name: string): SlashCommand["getArgumentCompletions"] {
	return LOCAL_ARGUMENT_COMPLETERS.get(name);
}

export function mergeKernelAndCliSlashCommands(kernelCommands: SlashCommand[]): SlashCommand[] {
	if (kernelCommands.length === 0) return BUILTIN_SLASH_COMMANDS;

	const merged = new Map<string, SlashCommand>();
	for (const command of CLI_ONLY_SLASH_COMMANDS) {
		merged.set(command.name, command);
	}
	for (const command of kernelCommands) {
		merged.set(command.name, {
			...command,
			getArgumentCompletions: command.getArgumentCompletions ?? getLocalArgumentCompleter(command.name),
		});
	}
	return [...merged.values()];
}

export async function loadSlashCommands(): Promise<SlashCommand[]> {
	return [];
}

function completeSessionArguments(argumentPrefix: string): Item[] | null {
	const [subcommand = "", value = ""] = argumentPrefix.split(/\s+/, 2);
	if (argumentPrefix.includes(" ") && subcommand === "delete") {
		return filterCompletions(value, [{ value: "confirm", label: "confirm", description: "Permanently delete current session" }]);
	}
	if (argumentPrefix.includes(" ") && (subcommand === "switch" || subcommand === "load")) return null;
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

function completeThinkingArguments(argumentPrefix: string): Item[] | null {
	if (argumentPrefix.includes(" ")) return null;
	return filterCompletions(argumentPrefix.trim(), [
		{ value: "on", label: "on", description: "Request thinking for future LLM calls" },
		{ value: "off", label: "off", description: "Disable thinking for future LLM calls" },
	]);
}

function filterCompletions(prefix: string, items: Item[]): Item[] | null {
	const normalized = prefix.toLowerCase();
	const filtered = items.filter(item => item.value.toLowerCase().startsWith(normalized));
	return filtered.length > 0 ? filtered : null;
}
