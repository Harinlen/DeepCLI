import { existsSync, readFileSync } from "node:fs";
import { cloneDefaultConfig, type CliConfig, type SessionListScope, type SessionStartupMode, type SymbolPresetName } from "@/config/schema.js";
import { CLIENT_CONFIG_PATH, expandHome } from "@/config/paths.js";
import type { CliArgs } from "@/startup/args.js";

export interface CliEnvironment {
  [key: string]: string | undefined;
  KERNEL_URL?: string;
  KERNEL_PORT?: string;
  MUSTANG_TOKEN?: string;
}

export interface LoadedCliConfig {
  config: CliConfig;
  path: string;
  warnings: string[];
}

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

export function loadCliConfig(options: {
  path?: string;
  env?: CliEnvironment;
  args?: Partial<CliArgs>;
} = {}): LoadedCliConfig {
  const path = expandHome(options.path ?? CLIENT_CONFIG_PATH);
  const env = options.env ?? process.env;
  const config = cloneDefaultConfig();
  const warnings: string[] = [];

  if (existsSync(path)) {
    mergeConfig(config, parseClientConfig(readFileSync(path, "utf8"), path), path);
  }

  applyEnvironment(config, env);
  applyArgs(config, options.args ?? {});
  validateConfig(config, path);

  return { config, path, warnings };
}

export function parseClientConfig(raw: string, path = CLIENT_CONFIG_PATH): Record<string, unknown> {
  if (!raw.trim()) return {};
  if (raw.trimStart().startsWith("{")) {
    try {
      return JSON.parse(raw) as Record<string, unknown>;
    } catch (error) {
      throw new ConfigError(`Failed to parse ${path}: ${(error as Error).message}`);
    }
  }
  return parseSimpleYaml(raw, path);
}

function parseSimpleYaml(raw: string, path: string): Record<string, unknown> {
  const root: Record<string, unknown> = {};
  let section: Record<string, unknown> | null = null;

  for (const [index, original] of raw.split(/\r?\n/).entries()) {
    const withoutComment = stripYamlComment(original);
    if (!withoutComment.trim()) continue;
    const indent = withoutComment.match(/^ */)?.[0].length ?? 0;
    const line = withoutComment.trim();
    const match = line.match(/^([A-Za-z0-9_-]+):(?:\s*(.*))?$/);
    if (!match) {
      throw new ConfigError(`Failed to parse ${path}:${index + 1}: expected "key: value"`);
    }
    const [, key, rawValue = ""] = match;
    if (indent === 0 && rawValue === "") {
      section = {};
      root[key] = section;
    } else if (indent === 0) {
      root[key] = parseScalar(rawValue);
      section = null;
    } else if (indent === 2 && section) {
      section[key] = parseScalar(rawValue);
    } else {
      throw new ConfigError(`Failed to parse ${path}:${index + 1}: only one nested mapping level is supported`);
    }
  }

  return root;
}

function stripYamlComment(line: string): string {
  let quote: string | null = null;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if ((ch === "\"" || ch === "'") && line[i - 1] !== "\\") quote = quote === ch ? null : quote ?? ch;
    if (ch === "#" && quote === null) return line.slice(0, i);
  }
  return line;
}

function parseScalar(value: string): unknown {
  const trimmed = value.trim();
  if (trimmed === "" || trimmed === "null" || trimmed === "~") return null;
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (/^-?\d+$/.test(trimmed)) return Number(trimmed);
  if ((trimmed.startsWith("\"") && trimmed.endsWith("\"")) || (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function mergeConfig(config: CliConfig, parsed: Record<string, unknown>, path: string): void {
  const allowedSections = new Set(["kernel", "session", "ui"]);
  for (const [sectionName, value] of Object.entries(parsed)) {
    if (!allowedSections.has(sectionName)) throw new ConfigError(`Invalid config field in ${path}: ${sectionName}`);
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new ConfigError(`Invalid config section in ${path}: ${sectionName}`);
    }
    const section = config[sectionName as keyof CliConfig] as Record<string, unknown>;
    for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
      if (!(key in section)) throw new ConfigError(`Invalid config field in ${path}: ${sectionName}.${key}`);
      section[key] = entry;
    }
  }
}

function applyEnvironment(config: CliConfig, env: CliEnvironment): void {
  if (env.KERNEL_URL) config.kernel.url = env.KERNEL_URL;
  if (env.KERNEL_PORT) config.kernel.url = `ws://localhost:${env.KERNEL_PORT}`;
  if (env.MUSTANG_TOKEN) config.kernel.token = env.MUSTANG_TOKEN;
}

function applyArgs(config: CliConfig, args: Partial<CliArgs>): void {
  if (args.kernelUrl) config.kernel.url = args.kernelUrl;
  if (args.port !== undefined) config.kernel.url = `ws://localhost:${args.port}`;
  if (args.theme) config.ui.theme = args.theme;
}

function validateConfig(config: CliConfig, path: string): void {
  assertString(config.kernel.url, `${path}: kernel.url`);
  assertNullableString(config.kernel.token, `${path}: kernel.token`);
  assertNullableString(config.kernel.token_file, `${path}: kernel.token_file`);
  assertBoolean(config.kernel.autostart, `${path}: kernel.autostart`);
  assertNullableString(config.kernel.autostart_command, `${path}: kernel.autostart_command`);
  assertString(config.kernel.health_url, `${path}: kernel.health_url`);

  if (!["picker", "last", "new"].includes(config.session.startup)) {
    throw new ConfigError(`${path}: session.startup must be picker, last, or new`);
  }
  if (!["cwd", "all"].includes(config.session.list_scope)) {
    throw new ConfigError(`${path}: session.list_scope must be cwd or all`);
  }
  assertBoolean(config.session.include_archived, `${path}: session.include_archived`);
  assertNumber(config.session.picker_limit, `${path}: session.picker_limit`);
  assertBoolean(config.session.restore_cwd, `${path}: session.restore_cwd`);

  assertString(config.ui.theme, `${path}: ui.theme`);
  assertBoolean(config.ui.auto_theme, `${path}: ui.auto_theme`);
  if (!["unicode", "nerd", "ascii"].includes(config.ui.symbols)) {
    throw new ConfigError(`${path}: ui.symbols must be unicode, nerd, or ascii`);
  }
  assertBoolean(config.ui.status_line, `${path}: ui.status_line`);
  assertNumber(config.ui.welcome_recent, `${path}: ui.welcome_recent`);
}

function assertString(value: unknown, field: string): asserts value is string {
  if (typeof value !== "string" || value.length === 0) throw new ConfigError(`${field} must be a non-empty string`);
}

function assertNullableString(value: unknown, field: string): asserts value is string | null {
  if (value !== null && typeof value !== "string") throw new ConfigError(`${field} must be a string or null`);
}

function assertBoolean(value: unknown, field: string): asserts value is boolean {
  if (typeof value !== "boolean") throw new ConfigError(`${field} must be true or false`);
}

function assertNumber(value: unknown, field: string): asserts value is number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new ConfigError(`${field} must be a number`);
}

export function coerceSessionStartupMode(value: string): SessionStartupMode {
  if (value === "picker" || value === "last" || value === "new") return value;
  throw new ConfigError(`Invalid session startup mode: ${value}`);
}

export function coerceSessionListScope(value: string): SessionListScope {
  if (value === "cwd" || value === "all") return value;
  throw new ConfigError(`Invalid session list scope: ${value}`);
}

export function coerceSymbolPreset(value: string): SymbolPresetName {
  if (value === "unicode" || value === "nerd" || value === "ascii") return value;
  throw new ConfigError(`Invalid symbol preset: ${value}`);
}
