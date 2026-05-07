/**
 * DeepCLI — ACP TUI client.
 *
 * Runtime boundary: the CLI talks to the kernel only through WebSocket ACP.
 */

import chalk from "chalk";
import { KernelNotRunning } from "@/acp/client.js";
import { ConfigError, loadCliConfig } from "@/config/loader.js";
import { InteractiveMode } from "@/modes/interactive.js";
import { SessionService } from "@/sessions/service.js";
import { connectKernel } from "@/startup/connect.js";
import { ArgError, parseCliArgs, usage } from "@/startup/args.js";
import { fetchKernelVersion } from "@/startup/health.js";
import { resolveStartupSession } from "@/startup/session-startup.js";
import { applyThemeConfig } from "@/startup/theme.js";
import type { MustangSession } from "@/session.js";

async function main(): Promise<void> {
  let args;
  try {
    args = parseCliArgs();
  } catch (error) {
    if (error instanceof ArgError) {
      console.error(chalk.red(error.message));
      console.error(usage());
      process.exit(2);
    }
    throw error;
  }

  if (args.help) {
    console.log(usage());
    return;
  }

  let loaded;
  try {
    loaded = loadCliConfig({ args });
  } catch (error) {
    if (error instanceof ConfigError) {
      console.error(chalk.red(error.message));
      process.exit(1);
    }
    throw error;
  }

  const themeResult = await applyThemeConfig(loaded.config);
  if (themeResult.warning) console.error(chalk.yellow(themeResult.warning));

  let connection;
  try {
    connection = await connectKernel(loaded.config);
  } catch (error) {
    if (error instanceof KernelNotRunning) {
      console.error(chalk.red(error.message));
    } else {
      console.error(chalk.red(`Connection failed: ${(error as Error).message}`));
    }
    process.exit(1);
  }

  const service = new SessionService(connection.client);
  const startup = await resolveStartupSession(service, args, loaded.config);
  if (startup.warning) console.error(chalk.yellow(startup.warning));
  const kernelVersion = await fetchKernelVersion(loaded.config.kernel.health_url);

  if (args.prompt || args.print) {
    if (!startup.session) {
      throw new Error("Prompt mode did not create a session.");
    }
    await runPrintPrompt(startup.session, args.prompt ?? "");
    connection.client.close();
    connection.autostarted?.stop();
    return;
  }

  await new InteractiveMode(connection.client, startup.session, {
    sessionService: service,
    recentSessions: startup.recentSessions.slice(0, loaded.config.ui.welcome_recent),
    theme: loaded.config.ui,
    version: kernelVersion ?? undefined,
    config: loaded.config,
    configPath: loaded.path,
  }).run();

  connection.autostarted?.stop();
}

async function runPrintPrompt(session: MustangSession, prompt: string): Promise<void> {
  if (!prompt.trim()) return;
  const skillInvocation = await resolvePrintSkillInvocation(session, prompt);
  const onUpdate = (update: any) => {
    if (update.sessionUpdate === "agent_message_chunk" && typeof update.content?.text === "string") {
      process.stdout.write(update.content.text);
    }
  };
  if (skillInvocation) {
    await session.activateSkill(skillInvocation.name, skillInvocation.args, onUpdate);
  } else {
    await session.prompt(prompt, onUpdate);
  }
  process.stdout.write("\n");
}

async function resolvePrintSkillInvocation(
  session: MustangSession,
  prompt: string,
): Promise<{ name: string; args: string } | undefined> {
  const trimmed = prompt.trimStart();
  const match = /^\/([^\s/]+)(?:\s+([\s\S]*))?$/.exec(trimmed);
  if (!match) return undefined;
  const name = match[1] ?? "";
  const commands = await session.listCommands().catch(() => []);
  if (!commands.some(command => command.name === name && command.source === "skill")) {
    return undefined;
  }
  return { name, args: match[2] ?? "" };
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
