import { homedir } from "node:os";
import { join, resolve, win32 } from "node:path";

export const DEEPCLI_HOME_DIRNAME = ".deepcli";

export const CLIENT_CONFIG_PATH = defaultClientConfigPath();
export const DEFAULT_TOKEN_FILE = defaultTokenFilePath();

export interface PathEnvironment {
  [key: string]: string | undefined;
  APPDATA?: string;
  DEEPCLI_CONFIG_DIR?: string;
  DEEPCLI_DATA_DIR?: string;
  DEEPCLI_HOME?: string;
  DEEPCLI_STATE_DIR?: string;
  LOCALAPPDATA?: string;
  XDG_CONFIG_HOME?: string;
  XDG_DATA_HOME?: string;
  XDG_STATE_HOME?: string;
}

export function expandHome(path: string): string {
  if (path === "~") return homedir();
  if (path.startsWith("~/")) return resolve(homedir(), path.slice(2));
  if (path.startsWith("~\\")) return resolve(homedir(), path.slice(2));
  return path;
}

export function defaultDeepCliHome(env: PathEnvironment = process.env): string {
  if (env.DEEPCLI_HOME) return expandHome(env.DEEPCLI_HOME);
  return join(homedir(), DEEPCLI_HOME_DIRNAME);
}

export function defaultConfigDir(
  env: PathEnvironment = process.env,
  platform: NodeJS.Platform = process.platform,
): string {
  if (env.DEEPCLI_CONFIG_DIR) return expandHome(env.DEEPCLI_CONFIG_DIR);
  return defaultDeepCliHome(env);
}

export function defaultStateDir(
  env: PathEnvironment = process.env,
  platform: NodeJS.Platform = process.platform,
): string {
  if (env.DEEPCLI_STATE_DIR) return expandHome(env.DEEPCLI_STATE_DIR);
  const joinPath = platform === "win32" ? win32.join : join;
  return joinPath(defaultDeepCliHome(env), "state");
}

export function defaultDataDir(
  env: PathEnvironment = process.env,
  platform: NodeJS.Platform = process.platform,
): string {
  if (env.DEEPCLI_DATA_DIR) return expandHome(env.DEEPCLI_DATA_DIR);
  const joinPath = platform === "win32" ? win32.join : join;
  return joinPath(defaultDeepCliHome(env), "data");
}

export function defaultClientConfigPath(
  env: PathEnvironment = process.env,
  platform: NodeJS.Platform = process.platform,
): string {
  const joinPath = platform === "win32" ? win32.join : join;
  return joinPath(defaultConfigDir(env, platform), "client.yaml");
}

export function resolveClientConfigPath(path: string | undefined, env: PathEnvironment = process.env): string {
  if (path) return expandHome(path);
  return defaultClientConfigPath(env);
}

export function defaultTokenFilePath(
  env: PathEnvironment = process.env,
  platform: NodeJS.Platform = process.platform,
): string {
  const joinPath = platform === "win32" ? win32.join : join;
  return joinPath(defaultStateDir(env, platform), "auth_token");
}

export function tokenFileCandidates(configuredPath: string | null = null, env: PathEnvironment = process.env): string[] {
  const candidates = [
    configuredPath ? expandHome(configuredPath) : null,
    defaultTokenFilePath(env),
  ].filter((path): path is string => Boolean(path));
  return [...new Set(candidates)];
}
