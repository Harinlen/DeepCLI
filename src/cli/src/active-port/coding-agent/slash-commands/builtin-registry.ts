// @ts-nocheck
import {
	getAvailableThemes,
	getCurrentThemeName,
	setTheme,
} from "../modes/theme/theme";

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
			case "auth":
				ctx.showWarning?.("/auth is not wired in the CLI TUI yet.");
				return true;
			case "compact":
				await ctx.handleCompactCommand?.(parsed.args ?? "");
				return true;
			case "cost":
				await ctx.handleUsageCommand?.();
				return true;
			case "memory":
				await ctx.handleMemoryCommand?.(`/memory ${parsed.args ?? ""}`.trim());
				return true;
			case "plan":
				await ctx.handlePlanModeCommand?.(parsed.args ?? "");
				return true;
			case "session":
				return await executeSessionCommand(ctx, parsed.args ?? "");
			case "model":
				return await executeModelCommand(ctx, parsed.args ?? "");
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
			ctx.showStatus?.(`Created session ${id}`);
			ctx.updateEditorTopBorder?.();
			return true;
		}
		case "switch":
		case "load": {
			const target = await resolveSessionTarget(ctx, args[1]);
			if (!target) {
				ctx.showWarning?.(`Usage: /session ${subcommand} <session-id>`);
				return true;
			}
			const id = await session.loadSession?.(target);
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
	const subcommand = args[0] ?? "list";
	if (subcommand === "switch" || subcommand === "set") {
		const profile = args[1];
		if (!profile) {
			ctx.showWarning?.(`Usage: /model ${subcommand} <profile>`);
			return true;
		}
		await ctx.session.setDefaultModelProfile?.(profile);
		ctx.statusLine?.invalidate?.();
		ctx.updateEditorTopBorder?.();
		ctx.showStatus?.(`Model set to ${profile}`);
		return true;
	}
	if (subcommand === "list") {
		await ctx.session.refreshModelProfiles?.();
		ctx.showStatus?.(`Current model: ${ctx.session.model?.name ?? "no-model"}`);
		return true;
	}
	ctx.showWarning?.("Usage: /model [list|switch|set]");
	return true;
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

function splitArgs(value: string): string[] {
	return value.trim() ? value.trim().split(/\s+/) : [];
}
