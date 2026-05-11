import {
  AcpError,
  KernelDisconnected,
  type AcpClient,
  type ExecutionResult,
  type PromptResult,
  type SessionUpdateParams,
} from "@/acp/client.js";
import { AcpMethod, MustangMethod } from "@/acp/methods.js";
import type { CliSessionInfo } from "@/sessions/types.js";
import { randomUUID } from "node:crypto";
import { cwd } from "process";

type MustangSessionClient = Pick<
  AcpClient,
  "request" | "notify" | "promptRequest" | "activateSkillRequest" | "executeShellRequest" | "executePythonRequest" | "onUpdate"
>;

export type PermissionMode = "default" | "accept_edits" | "plan" | "auto" | "dont_ask" | "bypass";

type SessionModeState = {
  configOptions?: unknown;
  config_options?: unknown;
  modes?: unknown;
};

export interface CostUsageReport {
  sessionId: string;
  title?: string | null;
  cwd: string;
  createdAt?: string | null;
  updatedAt?: string | null;
  model?: string | null;
  kernelVersion: string;
  tokens: { input: number; output: number; cacheRead: number; cacheWrite: number; total: number };
  context: {
    totalTokens: number;
    contextWindow?: number | null;
    percent: number;
    sections: Array<{ id: string; label: string; tokens: number; percent: number }>;
  };
  history: {
    messages: number;
    turns: number;
    toolCalls: number;
    compactions: number;
    queuedTurns: number;
    inFlight: boolean;
    lastRunAt?: string | null;
    lastDurationMs?: number | null;
  };
  memory: { loaded: number; writableScopes: number };
  environment: { lspServers: string[]; mcpServers: string[] };
  costUsd?: number | null;
  costNote?: string | null;
}

export interface CommandEntry {
  name: string;
  description: string;
  usage: string;
  acpMethod?: string | null;
  acp_method?: string | null;
  subcommands?: string[];
  source?: string;
}

export interface ListCommandsResponse {
  commands: CommandEntry[];
}

export interface RuntimeStatusReport {
  status: Record<string, unknown>;
}

const RESUME_RETRY_ATTEMPTS = 24;
const RESUME_RETRY_DELAY_MS = 250;

export class MustangSession {
  constructor(
    private client: MustangSessionClient,
    public readonly sessionId: string,
    public summary?: CliSessionInfo,
  ) {}

  static async create(
    client: AcpClient,
    workingDir?: string,
  ): Promise<MustangSession> {
    const result = await client.request<{ sessionId: string }>(AcpMethod.sessionNew, {
      cwd: workingDir ?? cwd(),
      mcpServers: [],
    });
    return new MustangSession(client, result.sessionId);
  }

  static async load(
    client: AcpClient,
    id: string,
    workingDir?: string,
  ): Promise<MustangSession> {
    await client.request(AcpMethod.sessionLoad, {
      sessionId: id,
      cwd: workingDir ?? cwd(),
      mcpServers: [],
    });
    return new MustangSession(client, id);
  }

  static async getUsage(client: AcpClient, sessionId?: string): Promise<CostUsageReport> {
    const params = sessionId
      ? {
          sessionId,
          session_id: sessionId,
        }
      : {};
    return await client.request<CostUsageReport>(MustangMethod.sessionGetUsage, params);
  }

  async prompt(
    text: string,
    onUpdate: (update: SessionUpdateParams) => void,
    options: { mode?: PermissionMode } = {},
  ): Promise<PromptResult> {
    const unsub = this.client.onUpdate(onUpdate);
    const clientTurnId = randomUUID();
    try {
      let resumeState = await this.resumeWithRetry();
      await this.syncModeAfterResume(options.mode, resumeState);
      try {
        return await this.client.promptRequest(this.sessionId, text, { clientTurnId });
      } catch (error) {
        if (!(error instanceof KernelDisconnected)) throw error;
        resumeState = await this.resumeWithRetry();
        await this.syncModeAfterResume(options.mode, resumeState);
        return await this.client.promptRequest(this.sessionId, text, { clientTurnId });
      }
    } finally {
      unsub();
    }
  }

  async activateSkill(
    skill: string,
    args: string,
    onUpdate: (update: SessionUpdateParams) => void,
    options: { mode?: PermissionMode } = {},
  ): Promise<PromptResult> {
    const unsub = this.client.onUpdate(onUpdate);
    const clientTurnId = randomUUID();
    try {
      let resumeState = await this.resumeWithRetry();
      await this.syncModeAfterResume(options.mode, resumeState);
      try {
        return await this.client.activateSkillRequest(this.sessionId, skill, args, { clientTurnId });
      } catch (error) {
        if (!(error instanceof KernelDisconnected)) throw error;
        resumeState = await this.resumeWithRetry();
        await this.syncModeAfterResume(options.mode, resumeState);
        return await this.client.activateSkillRequest(this.sessionId, skill, args, { clientTurnId });
      }
    } finally {
      unsub();
    }
  }

  async listCommands(): Promise<CommandEntry[]> {
    const result = await this.client.request<ListCommandsResponse>(
      MustangMethod.commandsList,
      {},
    );
    return result.commands ?? [];
  }

  async executeShell(
    command: string,
    excludeFromContext: boolean,
    onUpdate: (update: SessionUpdateParams) => void,
  ): Promise<ExecutionResult> {
    const unsub = this.client.onUpdate(onUpdate);
    try {
      return await this.client.executeShellRequest(this.sessionId, command, excludeFromContext);
    } finally {
      unsub();
    }
  }

  async executePython(
    code: string,
    excludeFromContext: boolean,
    onUpdate: (update: SessionUpdateParams) => void,
  ): Promise<ExecutionResult> {
    const unsub = this.client.onUpdate(onUpdate);
    try {
      return await this.client.executePythonRequest(this.sessionId, code, excludeFromContext);
    } finally {
      unsub();
    }
  }

  cancel(): void {
    this.client.notify(AcpMethod.sessionCancel, { sessionId: this.sessionId });
  }

  cancelExecution(kind: "shell" | "python" | "any" = "any"): void {
    this.client.notify(MustangMethod.sessionCancelExecution, { sessionId: this.sessionId, kind });
  }

  async setMode(mode: PermissionMode): Promise<void> {
    await this.client.request(AcpMethod.sessionSetMode, {
      sessionId: this.sessionId,
      modeId: mode,
    });
  }

  async getUsage(): Promise<CostUsageReport> {
    // Send both spellings for compatibility with already-running dev kernels
    // that may have loaded the new method before the ACP camelCase base model.
    return await MustangSession.getUsage(this.client as AcpClient, this.sessionId);
  }

  async runtimeStatus(): Promise<RuntimeStatusReport> {
    return await this.client.request<RuntimeStatusReport>(MustangMethod.runtimeStatus, {});
  }

  async runtimeRestart(reason = "user requested runtime restart"): Promise<RuntimeStatusReport> {
    return await this.client.request<RuntimeStatusReport>(MustangMethod.runtimeRestart, { reason });
  }

  private async resumeWithRetry(): Promise<SessionModeState> {
    let lastError: unknown;
    for (let attempt = 1; attempt <= RESUME_RETRY_ATTEMPTS; attempt++) {
      try {
        return await this.resume();
      } catch (error) {
        lastError = error;
        if (!isTransientResumeError(error) || attempt === RESUME_RETRY_ATTEMPTS) break;
        await sleep(RESUME_RETRY_DELAY_MS);
      }
    }
    throw lastError;
  }

  private async resume(): Promise<SessionModeState> {
    return await this.client.request<SessionModeState>(AcpMethod.sessionResume, {
      sessionId: this.sessionId,
      cwd: cwd(),
    });
  }

  private async syncModeAfterResume(
    desiredMode: PermissionMode | undefined,
    resumeState: SessionModeState,
  ): Promise<void> {
    if (!desiredMode) return;
    if (extractPermissionMode(resumeState) === desiredMode) return;
    await this.setMode(desiredMode);
  }
}

function isTransientResumeError(error: unknown): boolean {
  return error instanceof AcpError && error.code === -32603 && error.message.includes("Internal error");
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function extractPermissionMode(value: unknown): PermissionMode | undefined {
  const item = value as Record<string, unknown> | undefined;
  const modes = item?.modes as { currentModeId?: unknown; current_mode_id?: unknown } | undefined;
  const fromModes = parsePermissionMode(modes?.currentModeId ?? modes?.current_mode_id);
  if (fromModes) return fromModes;

  const configOptions = Array.isArray(item?.configOptions)
    ? item.configOptions
    : Array.isArray(item?.config_options)
      ? item.config_options
      : undefined;
  const modeConfig = configOptions?.find(option => {
    const record = option as { configId?: unknown; config_id?: unknown } | undefined;
    return record?.configId === "mode" || record?.config_id === "mode";
  }) as { currentValue?: unknown; current_value?: unknown } | undefined;
  const fromConfig = parsePermissionMode(modeConfig?.currentValue ?? modeConfig?.current_value);
  if (fromConfig) return fromConfig;

  return item && "raw" in item ? extractPermissionMode(item.raw) : undefined;
}

function parsePermissionMode(value: unknown): PermissionMode | undefined {
  const modes: PermissionMode[] = ["default", "accept_edits", "plan", "auto", "dont_ask", "bypass"];
  return typeof value === "string" && modes.includes(value as PermissionMode)
    ? value as PermissionMode
    : undefined;
}
