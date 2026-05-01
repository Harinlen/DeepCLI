import { cwd as processCwd } from "node:process";
import type { CliConfig } from "@/config/schema.js";
import type { CliArgs } from "@/startup/args.js";
import { MustangSession } from "@/session.js";
import type { CliSessionInfo } from "@/sessions/types.js";
import { SessionService } from "@/sessions/service.js";
import { promptForSessionSelection } from "@/sessions/terminal-picker.js";

export interface StartupSessionResult {
  session: MustangSession;
  recentSessions: CliSessionInfo[];
  warning?: string;
}

export async function resolveStartupSession(
  service: SessionService,
  args: CliArgs,
  config: CliConfig,
  options: { isInteractive?: boolean; cwd?: string } = {},
): Promise<StartupSessionResult> {
  const cwd = options.cwd ?? processCwd();
  const isInteractive = options.isInteractive ?? process.stdin.isTTY;
  const listCwd = config.session.list_scope === "cwd" ? cwd : undefined;

  if (args.sessionId) {
    await service.load(args.sessionId, cwd);
    const recentSessions = await safeList(service, config, listCwd);
    return { session: new MustangSession(service.clientForSession(), args.sessionId), recentSessions };
  }

  const mustAvoidPicker = args.print || Boolean(args.prompt) || !isInteractive;
  if (args.newSession || mustAvoidPicker || config.session.startup === "new") {
    return createNew(service, config, listCwd, cwd);
  }

  const recentSessions = await safeList(service, config, listCwd);
  if (config.session.startup === "picker") {
    const picked = await promptForSessionSelection(recentSessions);
    if (picked !== "cancel" && picked !== "new") {
      try {
        await service.load(picked.sessionId, config.session.restore_cwd && picked.cwd ? picked.cwd : cwd);
        return { session: new MustangSession(service.clientForSession(), picked.sessionId, picked), recentSessions };
      } catch (error) {
        const created = await createNew(service, config, listCwd, cwd, recentSessions);
        return { ...created, warning: `Failed to load selected session; created a new one. ${(error as Error).message}` };
      }
    }
    if (picked === "cancel") {
      return createNew(service, config, listCwd, cwd, recentSessions);
    }
  }
  const candidate = config.session.startup === "last" || config.session.startup === "picker"
    ? recentSessions[0]
    : undefined;
  if (candidate) {
    try {
      await service.load(candidate.sessionId, config.session.restore_cwd && candidate.cwd ? candidate.cwd : cwd);
      return { session: new MustangSession(service.clientForSession(), candidate.sessionId, candidate), recentSessions };
    } catch (error) {
      const created = await createNew(service, config, listCwd, cwd);
      return { ...created, warning: `Failed to load recent session; created a new one. ${(error as Error).message}` };
    }
  }

  return createNew(service, config, listCwd, cwd, recentSessions);
}

async function createNew(
  service: SessionService,
  config: CliConfig,
  listCwd: string | undefined,
  cwd: string,
  recentSessions?: CliSessionInfo[],
): Promise<StartupSessionResult> {
  const result = await service.create(cwd);
  return {
    session: new MustangSession(service.clientForSession(), result.sessionId),
    recentSessions: recentSessions ?? await safeList(service, config, listCwd),
  };
}

async function safeList(service: SessionService, config: CliConfig, cwd: string | undefined): Promise<CliSessionInfo[]> {
  try {
    return await service.list({
      cwd,
      includeArchived: config.session.include_archived,
      limit: Math.max(config.ui.welcome_recent, config.session.picker_limit),
    });
  } catch {
    return [];
  }
}
