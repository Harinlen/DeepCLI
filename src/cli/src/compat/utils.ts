import * as path from "node:path";
import { readFileSync } from "node:fs";

export const APP_NAME = "DeepCLI";
export const CONFIG_DIR_NAME = ".mustang";
export const VERSION = "1.0.0";
export const TUI_VERSION = "1.0.8";

export const $env = new Proxy({}, { get: (_target, prop) => process.env[String(prop)] }) as Record<string, string | undefined>;
export function $flag(name: string): boolean { return process.argv.includes(`--${name}`); }
export function $which(_name: string): string | undefined { return undefined; }
export function $envpos(_name: string): number | undefined { return undefined; }

export const logger = {
	debug: (...args: unknown[]) => console.debug(...args),
	info: (...args: unknown[]) => console.info(...args),
	warn: (...args: unknown[]) => console.warn(...args),
	error: (...args: unknown[]) => console.error(...args),
	time: async (_label: string, fn?: (...args: any[]) => unknown, ...args: any[]) => {
		if (typeof fn === "function") return await fn(...args);
		return { end: () => {} };
	},
};

let projectDir = process.cwd();
export function getProjectDir(): string { return projectDir; }
export function setProjectDir(value: string): void { projectDir = value; }
export function getAgentDir(): string { return path.join(process.cwd(), ".mustang"); }
export function getConfigDirName(): string { return ".mustang"; }
export function getConfigRootDir(): string { return getAgentDir(); }
export function getAgentDbPath(): string { return path.join(getAgentDir(), "agent.db"); }
export function getDebugLogPath(): string { return path.join(getAgentDir(), "debug.log"); }
export function getLogPath(): string { return getDebugLogPath(); }
export function getLogsDir(): string { return path.dirname(getDebugLogPath()); }
export function getReportsDir(): string { return path.join(getAgentDir(), "reports"); }
export function getSessionsDir(): string { return path.join(getAgentDir(), "sessions"); }
export function getPluginsDir(): string { return path.join(getAgentDir(), "plugins"); }
export function getSSHConfigPath(): string { return path.join(getAgentDir(), "ssh.json"); }
export function getCustomThemesDir(): string { return path.join(getAgentDir(), "themes"); }
export function getPythonEnvDir(): string { return path.join(getAgentDir(), "python"); }
export function getMemoriesDir(): string { return path.join(getAgentDir(), "memories"); }

let defaultTabWidth = 4;
export function getDefaultTabWidth(): number { return defaultTabWidth; }
export function setDefaultTabWidth(width: number): void { defaultTabWidth = width; }
export function getIndentation(_file?: string, _cwd?: string): number { return defaultTabWidth; }

export function formatNumber(value: number): string { return Number(value ?? 0).toLocaleString(); }
export function formatCount(value: number): string { return formatNumber(value); }
export function formatCompactNumber(value: number): string {
	const number = Number(value ?? 0);
	if (!Number.isFinite(number)) return "0";
	const sign = number < 0 ? "-" : "";
	const abs = Math.abs(number);
	if (abs >= 1_000_000) return `${sign}${trimCompactNumber(abs / 1_000_000)}M`;
	if (abs >= 1_000) return `${sign}${trimCompactNumber(abs / 1_000)}K`;
	return `${sign}${Math.round(abs).toLocaleString()}`;
}
export function formatDuration(ms: number): string { return `${Math.round(ms / 1000)}s`; }
export function formatAge(_date: Date | number | string): string { return ""; }
export function formatBytes(bytes: number): string { return `${bytes} B`; }
export function pluralize(word: string, count: number): string { return count === 1 ? word : `${word}s`; }
export function relativePathWithinRoot(root: string, value: string): string {
	const rel = path.relative(root, value);
	return rel && !rel.startsWith("..") ? rel : value;
}
export function pathIsWithin(parent: string, child: string): boolean {
	const rel = path.relative(parent, child);
	return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

export function isEnoent(error: unknown): boolean { return (error as { code?: string })?.code === "ENOENT"; }
export function hasFsCode(error: unknown, code: string): boolean { return (error as { code?: string })?.code === code; }
export function toError(error: unknown): Error { return error instanceof Error ? error : new Error(String(error)); }
function trimCompactNumber(value: number): string {
	return value.toFixed(value >= 10 ? 0 : 1).replace(/\.0$/, "");
}
export function peekFile(file: string, bytes = 4096): string {
	try {
		return readFileSync(file, "utf8").slice(0, bytes);
	} catch {
		return "";
	}
}
export const postmortem = { add: () => {}, remove: () => {}, register: () => () => {}, quit: async (code = 0) => process.exit(code) };
export async function prompt(_message: string): Promise<string> { return ""; }
prompt.render = (message: string) => message;

export function adjustHsv(hex: string): string { return hex; }
export function hsvToRgb(input?: { h?: number; s?: number; v?: number }): { r: number; g: number; b: number } {
	const h = ((input?.h ?? 0) % 360) / 60;
	const s = input?.s ?? 0;
	const v = input?.v ?? 1;
	const c = v * s;
	const x = c * (1 - Math.abs((h % 2) - 1));
	const m = v - c;
	const [rp, gp, bp] =
		h < 1 ? [c, x, 0] :
		h < 2 ? [x, c, 0] :
		h < 3 ? [0, c, x] :
		h < 4 ? [0, x, c] :
		h < 5 ? [x, 0, c] :
		[c, 0, x];
	return {
		r: Math.round((rp + m) * 255),
		g: Math.round((gp + m) * 255),
		b: Math.round((bp + m) * 255),
	};
}

export const procmgr = {
	add: () => {},
	remove: () => {},
	getShellConfig: () => ({}),
};

export class Snowflake {
	static next(): string { return `${Date.now()}`; }
	next(): string { return Snowflake.next(); }
}

export function parseFrontmatter(content: string): { attributes: Record<string, unknown>; body: string } {
	return { attributes: {}, body: content };
}

export function tryParseJson<T = unknown>(value: string): T | undefined {
	try {
		return JSON.parse(value) as T;
	} catch {
		return undefined;
	}
}

export function parseJsonlLenient(text: string): unknown[] {
	return text.split(/\r?\n/).filter(Boolean).map(line => tryParseJson(line)).filter(value => value !== undefined);
}

export async function abortableSleep(ms: number): Promise<void> {
	await new Promise(resolve => setTimeout(resolve, ms));
}

export function setNativeKillTree(_fn: unknown): void {}

export const SUPPORTED_IMAGE_MIME_TYPES = ["image/png", "image/jpeg", "image/webp"];
export async function readImageMetadata(): Promise<{ width: number; height: number; mimeType: string } | null> { return null; }

export async function renderMermaidAsciiSafe(_source: string): Promise<string | null> {
	return null;
}
