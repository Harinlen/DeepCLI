import { homedir } from "node:os";
import { resolve } from "node:path";

export const CLIENT_CONFIG_PATH = "~/.mustang/client.yaml";
export const DEFAULT_TOKEN_FILE = "~/.mustang/state/auth_token";

export function expandHome(path: string): string {
  if (path === "~") return homedir();
  if (path.startsWith("~/")) return resolve(homedir(), path.slice(2));
  return path;
}

