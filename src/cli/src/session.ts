import {
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
      await this.resume();
      try {
        return await this.client.promptRequest(this.sessionId, text, { clientTurnId });
      } catch (error) {
        if (!(error instanceof KernelDisconnected)) throw error;
        await this.resume();
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

  async setMode(mode: "default" | "plan"): Promise<void> {
    await this.client.request(AcpMethod.sessionSetMode, {
      sessionId: this.sessionId,
      modeId: mode,
    });
  }

  private async resume(): Promise<void> {
    await this.client.request(AcpMethod.sessionResume, {
      sessionId: this.sessionId,
      cwd: cwd(),
    });
  }
}
