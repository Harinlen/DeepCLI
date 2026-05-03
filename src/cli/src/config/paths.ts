import { homedir } from "node:os";
import { join, resolve, win32 } from "node:path";

export const LEGACY_CLIENT_CONFIG_PATH = "~/.mustang/client.yaml";
export const LEGACY_TOKEN_FILE = "~/.mustang/state/auth_token";

export const CLIENT_CONFIG_PATH = defaultClientConfigPath();
export const DEFAULT_TOKEN_FILE = defaultTokenFilePath();

export interface PathEnvironment {
  [key: string]: string | undefined;
  APPDATA?: string;
  DEEPCLI_CONFIG_DIR?: string;
  DEEPCLI_DATA_DIR?: string;
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

export function defaultConfigDir(
  env: PathEnvironment = process.env,
  platform: NodeJS.Platform = process.platform,
): string {
  if (env.DEEPCLI_CONFIG_DIR) return expandHome(env.DEEPCLI_CONFIG_DIR);
  if (platform === "win32") {
    return win32.join(env.APPDATA ?? win32.join(homedir(), "AppData", "Roaming"), "DeepCLI");
  }
  return join(env.XDG_CONFIG_HOME ?? join(homedir(), ".config"), "deepcli");
}

export function defaultStateDir(
  env: PathEnvironment = process.env,
  platform: NodeJS.Platform = process.platform,
): string {
  if (env.DEEPCLI_STATE_DIR) return expandHome(env.DEEPCLI_STATE_DIR);
  if (platform === "win32") {
    return win32.join(env.LOCALAPPDATA ?? win32.join(homedir(), "AppData", "Local"), "DeepCLI", "State");
  }
  return join(env.XDG_STATE_HOME ?? join(homedir(), ".local", "state"), "deepcli");
}

export function defaultDataDir(
  env: PathEnvironment = process.env,
  platform: NodeJS.Platform = process.platform,
): string {
  if (env.DEEPCLI_DATA_DIR) return expandHome(env.DEEPCLI_DATA_DIR);
  if (platform === "win32") {
    return win32.join(env.LOCALAPPDATA ?? win32.join(homedir(), "AppData", "Local"), "DeepCLI");
  }
  return join(env.XDG_DATA_HOME ?? join(homedir(), ".local", "share"), "deepcli");
}

export function defaultClientConfigPath(
  env: PathEnvironment = process.env,
  platform: NodeJS.Platform = process.platform,
): string {
  const joinPath = platform === "win32" ? win32.join : join;
  return joinPath(defaultConfigDir(env, platform), "client.yaml");
}

export function defaultTokenFilePath(
  env: PathEnvironment = process.env,
  platform: NodeJS.Platform = process.platform,
): string {
  const joinPath = platform === "win32" ? win32.join : join;
  return joinPath(defaultStateDir(env, platform), "auth_token");
}
