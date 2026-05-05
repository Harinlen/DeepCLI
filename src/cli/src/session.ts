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
  "request" | "notify" | "promptRequest" | "executeShellRequest" | "executePythonRequest" | "onUpdate"
>;

export type PermissionMode = "default" | "accept_edits" | "plan" | "auto" | "dont_ask" | "bypass";

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

  async prompt(
    text: string,
    onUpdate: (update: SessionUpdateParams) => void,
  ): Promise<PromptResult> {
    const unsub = this.client.onUpdate(onUpdate);
    const clientTurnId = randomUUID();
    try {
      await this.resumeWithRetry();
      try {
        return await this.client.promptRequest(this.sessionId, text, { clientTurnId });
      } catch (error) {
        if (!(error instanceof KernelDisconnected)) throw error;
        await this.resumeWithRetry();
        return await this.client.promptRequest(this.sessionId, text, { clientTurnId });
      }
    } finally {
      unsub();
    }
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
    return await this.client.request<CostUsageReport>(MustangMethod.sessionGetUsage, {
      sessionId: this.sessionId,
      session_id: this.sessionId,
    });
  }

  private async resumeWithRetry(): Promise<void> {
    let lastError: unknown;
    for (let attempt = 1; attempt <= RESUME_RETRY_ATTEMPTS; attempt++) {
      try {
        await this.resume();
        return;
      } catch (error) {
        lastError = error;
        if (!isTransientResumeError(error) || attempt === RESUME_RETRY_ATTEMPTS) break;
        await sleep(RESUME_RETRY_DELAY_MS);
      }
    }
    throw lastError;
  }

  private async resume(): Promise<void> {
    await this.client.request(AcpMethod.sessionResume, {
      sessionId: this.sessionId,
      cwd: cwd(),
    });
  }
}

function isTransientResumeError(error: unknown): boolean {
  return error instanceof AcpError && error.code === -32603 && error.message.includes("Internal error");
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
