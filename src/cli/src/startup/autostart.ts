import { spawn, type ChildProcess } from "node:child_process";
import type { CliConfig } from "@/config/schema.js";

export interface KernelProcessHandle {
  process: ChildProcess;
  stop(): void;
}

export async function maybeAutostartKernel(config: CliConfig, options: {
  connect: () => Promise<unknown>;
  spawnProcess?: (command: string, args: string[]) => ChildProcess;
  waitMs?: number;
}): Promise<KernelProcessHandle> {
  if (!config.kernel.autostart) throw new Error("Kernel autostart is disabled");
  if (!isLoopbackWsUrl(config.kernel.url)) {
    throw new Error(`Refusing to autostart kernel for non-loopback URL: ${config.kernel.url}`);
  }
  const commandLine = config.kernel.autostart_command;
  if (!commandLine) {
    throw new Error("kernel.autostart_command is required for CLI autostart");
  }

  const [command, ...args] = splitCommand(commandLine);
  const child = (options.spawnProcess ?? defaultSpawn)(command, args);
  const handle: KernelProcessHandle = {
    process: child,
    stop: () => {
      if (!child.killed) child.kill("SIGTERM");
    },
  };

  const deadline = Date.now() + (options.waitMs ?? 15_000);
  let lastError: unknown;
  while (Date.now() < deadline) {
    try {
      const ready = await options.connect();
      (ready as { close?: () => void } | undefined)?.close?.();
      return handle;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  handle.stop();
  throw new Error(`Kernel autostart timed out: ${(lastError as Error | undefined)?.message ?? "not ready"}`);
}

export function isLoopbackWsUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "ws:" || parsed.protocol === "wss:"
      ? ["localhost", "127.0.0.1", "::1", "[::1]"].includes(parsed.hostname)
      : false;
  } catch {
    return false;
  }
}

function defaultSpawn(command: string, args: string[]): ChildProcess {
  return spawn(command, args, {
    detached: false,
    stdio: "ignore",
  });
}

function splitCommand(commandLine: string): string[] {
  const parts = commandLine.match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g) ?? [];
  return parts.map((part) => part.replace(/^["']|["']$/g, ""));
}
