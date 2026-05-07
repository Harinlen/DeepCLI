import { existsSync, readFileSync } from "node:fs";
import { AcpClient, KernelNotRunning } from "@/acp/client.js";
import type { CliConfig } from "@/config/schema.js";
import { tokenFileCandidates } from "@/config/paths.js";
import { maybeAutostartKernel, type KernelProcessHandle } from "@/startup/autostart.js";

export interface ConnectResult {
  client: AcpClient;
  autostarted?: KernelProcessHandle;
}

export function resolveToken(config: CliConfig, env = process.env): string {
  if (env.DEEPCLI_TOKEN) return env.DEEPCLI_TOKEN;
  if (env.MUSTANG_TOKEN) return env.MUSTANG_TOKEN;
  if (config.kernel.token) return config.kernel.token;
  for (const path of tokenFileCandidates(config.kernel.token_file, env)) {
    if (existsSync(path)) {
      const token = readFileSync(path, "utf8").trim();
      if (token) return token;
    }
  }
  throw new Error(
    "No DeepCLI auth token found. Set DEEPCLI_TOKEN, set MUSTANG_TOKEN, "
    + "or configure kernel.token_file in the DeepCLI client config.",
  );
}

export async function connectKernel(config: CliConfig, options: {
  env?: NodeJS.ProcessEnv;
  connect?: typeof AcpClient.connect;
} = {}): Promise<ConnectResult> {
  const token = resolveToken(config, options.env ?? process.env);
  const connect = options.connect ?? AcpClient.connect;

  try {
    return { client: await connect(config.kernel.url, token) };
  } catch (error) {
    if (!(error instanceof KernelNotRunning) || !config.kernel.autostart) throw error;
    const autostarted = await maybeAutostartKernel(config, {
      connect: () => connect(config.kernel.url, token),
    });
    return { client: await connect(config.kernel.url, token), autostarted };
  }
}
