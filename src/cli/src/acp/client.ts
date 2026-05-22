/**
 * ACP WebSocket client — JSON-RPC 2.0 over WebSocket.
 *
 * Protocol quirks:
 * - Auth via URL query param: ?token=xxx or ?password=xxx
 * - Must send `initialize` after connect before any session/* calls
 * - session/prompt response arrives BEFORE streaming session/update chunks
 * - session/request_permission is a kernel-initiated request; we reply with
 *   a JSON-RPC response (not a notification)
 */

import WebSocket from "ws";
import { AcpMethod, MustangMethod } from "@/acp/methods.js";
import { DEFAULT_TOKEN_FILE, tokenFileCandidates } from "@/config/paths.js";
import { readFileSync } from "fs";

// ---------------------------------------------------------------------------
// Wire types (camelCase, matches kernel ACP schema)
// ---------------------------------------------------------------------------

export interface SessionUpdateParams {
  sessionUpdate: string;
  sessionId: string;
  [key: string]: unknown;
}

export interface PermissionRequest {
  reqId: number;
  sessionId: string;
  toolCall: {
    toolCallId: string;
    title?: string;
    inputSummary?: string;
  };
  options: Array<{ optionId: string; name: string; kind: string }>;
  toolInput?: Record<string, unknown>;
}

export interface PermissionResult {
  outcome: {
    outcome: "selected" | "cancelled";
    optionId?: string;
    updatedInput?: Record<string, unknown>;
  };
}

export interface PromptResult {
  stopReason: string;
  _meta?: Record<string, unknown>;
  meta?: Record<string, unknown>;
}

export interface PromptRequestOptions {
  clientTurnId?: string;
}

export interface ExecutionResult {
  exitCode: number;
  cancelled: boolean;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export class AcpError extends Error {
  constructor(
    public code: number,
    message: string,
  ) {
    super(`[${code}] ${message}`);
    this.name = "AcpError";
  }
}

export class KernelNotRunning extends Error {
  constructor(url: string) {
    super(`Cannot connect to kernel at ${url}. Is the kernel running?`);
    this.name = "KernelNotRunning";
  }
}

export class KernelDisconnected extends Error {
  constructor(message = "Kernel connection lost. Restart the kernel and reconnect the CLI.") {
    super(message);
    this.name = "KernelDisconnected";
  }
}

// ---------------------------------------------------------------------------
// AcpClient
// ---------------------------------------------------------------------------

type UpdateHandler = (params: SessionUpdateParams) => void;
type DisconnectHandler = (error: KernelDisconnected) => void;
type ReconnectHandler = () => void;
export type KernelConnectionState = "connected" | "connecting" | "disconnected";
type ConnectionStateHandler = (state: KernelConnectionState) => void;
type TokenProvider = () => string;
type PermissionHandler = (
  id: number,
  req: PermissionRequest,
) => Promise<PermissionResult>;

export class AcpClient {
  private reqId = 0;
  private pending = new Map<
    number,
    { resolve: (v: unknown) => void; reject: (e: Error) => void }
  >();
  private updateHandlers = new Set<UpdateHandler>();
  private disconnectHandlers = new Set<DisconnectHandler>();
  private reconnectHandlers = new Set<ReconnectHandler>();
  private connectionStateHandlers = new Set<ConnectionStateHandler>();
  private permissionHandler?: PermissionHandler;
  private disconnected?: KernelDisconnected;
  private reconnecting?: Promise<void>;
  private connectionState: KernelConnectionState = "connected";
  private closing = false;
  private healthTimer?: ReturnType<typeof setInterval>;

  private constructor(
    private ws: WebSocket,
    private readonly url: string,
    private readonly token: string,
    private readonly tokenProvider?: TokenProvider,
  ) {
    this.attachWebSocket(ws);
  }

  private attachWebSocket(ws: WebSocket): void {
    ws.on("message", (raw) => {
      if (ws !== this.ws) return;
      try {
        this.handleIncoming(JSON.parse(raw.toString()));
      } catch (e) {
        console.error("[acp] failed to parse frame:", e);
      }
    });
    ws.on("close", (code, reason) => {
      if (ws !== this.ws) return;
      if (this.closing) {
        this.stopHealthTimer();
        this.rejectPending(new KernelDisconnected("Kernel connection closed."));
        return;
      }
      const detail = reason.length > 0 ? ` (${reason.toString()})` : "";
      this.markDisconnected(
        new KernelDisconnected(`Kernel connection lost (close ${code})${detail}.`),
      );
    });
    ws.on("error", (error) => {
      if (ws !== this.ws) return;
      if (this.closing) return;
      this.markDisconnected(
        new KernelDisconnected(`Kernel connection error: ${(error as Error).message}`),
      );
    });
    this.startHealthTimer();
  }

  private startHealthTimer(): void {
    this.stopHealthTimer();
    this.healthTimer = setInterval(() => {
      if (this.disconnected || this.closing) return;
      if (this.ws.readyState === WebSocket.CLOSING || this.ws.readyState === WebSocket.CLOSED) {
        this.markDisconnected(
          new KernelDisconnected(`Kernel connection lost (state=${this.ws.readyState}).`),
        );
      }
    }, 250);
    this.healthTimer.unref?.();
  }

  private stopHealthTimer(): void {
    if (!this.healthTimer) return;
    clearInterval(this.healthTimer);
    this.healthTimer = undefined;
  }

  // ------------------------------------------------------------------
  // Connection
  // ------------------------------------------------------------------

  static async connect(
    url: string,
    token: string,
    options: { tokenProvider?: TokenProvider } = {},
  ): Promise<AcpClient> {
    const ws = await AcpClient.openWebSocket(url, token);
    const client = new AcpClient(ws, url, token, options.tokenProvider);

    // Must initialize before any session/* calls
    await client.initialize();

    return client;
  }

  private static async openWebSocket(url: string, token: string): Promise<WebSocket> {
    const base = url.replace(/\/$/, "");
    const wsUrl = `${base}/session?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(wsUrl);

    // Permanent error sink — prevents unhandled error events after once() fires.
    ws.on("error", () => {});

    try {
      await new Promise<void>((resolve, reject) => {
        ws.once("open", resolve);
        ws.once("error", () => reject(new KernelNotRunning(url)));
      });
    } catch (e) {
      ws.terminate();
      throw e;
    }
    return ws;
  }

  private async initialize(): Promise<void> {
    await this.request("initialize", {
      protocolVersion: 1,
      clientCapabilities: {},
      clientInfo: { name: "deepcli-cli", version: "1.0.0" },
    }, { skipReconnect: true });
  }

  close(): void {
    this.closing = true;
    this.setConnectionState("disconnected");
    this.stopHealthTimer();
    this.ws.close();
    this.rejectPending(new KernelDisconnected("Kernel connection closed."));
  }

  // ------------------------------------------------------------------
  // Inbound routing
  // ------------------------------------------------------------------

  private handleIncoming(msg: Record<string, unknown>): void {
    if ("id" in msg && ("result" in msg || "error" in msg)) {
      // JSON-RPC response to one of our requests
      this.routeResponse(msg);
    } else if (msg.method === "session/update") {
      const params = msg.params as { sessionId: string; update: SessionUpdateParams; _meta?: Record<string, unknown>; meta?: Record<string, unknown> };
      const meta = params._meta ?? params.meta;
      const update = meta ? { ...params.update, _meta: meta, meta } : params.update;
      for (const h of this.updateHandlers) h(update);
    } else if (msg.method === MustangMethod.sessionExecutionUpdate) {
      const params = msg.params as { sessionId?: string; execution?: Record<string, unknown> };
      const update = executionToSessionUpdate(params);
      for (const h of this.updateHandlers) h(update);
    } else if (msg.method === "session/request_permission") {
      // Kernel-initiated request — must reply with a response
      this.handlePermission(msg);
    }
  }

  private routeResponse(msg: Record<string, unknown>): void {
    const id = msg.id as number;
    const entry = this.pending.get(id);
    if (!entry) return;
    this.pending.delete(id);

    if ("error" in msg) {
      const err = msg.error as { code: number; message: string };
      entry.reject(new AcpError(err.code, err.message));
    } else {
      entry.resolve(msg.result);
    }
  }

  private async handlePermission(msg: Record<string, unknown>): Promise<void> {
    const id = msg.id as number;
    const params = msg.params as {
      sessionId: string;
      toolCall: PermissionRequest["toolCall"];
      options: PermissionRequest["options"];
      toolInput?: Record<string, unknown>;
    };

    const req: PermissionRequest = {
      reqId: id,
      sessionId: params.sessionId,
      toolCall: params.toolCall,
      options: params.options,
      toolInput: params.toolInput,
    };

    let result: PermissionResult;
    if (this.permissionHandler) {
      try {
        result = await this.permissionHandler(id, req);
      } catch {
        result = failClosedPermissionResult(req);
      }
    } else {
      result = failClosedPermissionResult(req);
    }

    try {
      this.respond(id, result);
    } catch (error) {
      if (!(error instanceof KernelDisconnected)) {
        console.error("[acp] failed to respond to permission request:", error);
      }
    }
  }

  // ------------------------------------------------------------------
  // Outbound helpers
  // ------------------------------------------------------------------

  private nextId(): number {
    return ++this.reqId;
  }

  private send(msg: unknown): void {
    this.assertConnected();
    this.ws.send(JSON.stringify(msg));
  }

  /** Send a JSON-RPC response to a kernel-initiated request. */
  respond(id: number, result: unknown): void {
    this.send({ jsonrpc: "2.0", id, result });
  }

  /** Send a request and await the response. Rejects on JSON-RPC error. */
  async request<R = unknown>(
    method: string,
    params: unknown,
    opts: { timeoutMs?: number; skipReconnect?: boolean } = {},
  ): Promise<R> {
    if (opts.skipReconnect) {
      this.assertConnected();
    } else {
      await this.ensureConnected();
    }
    const id = this.nextId();
    const timeoutMs = opts.timeoutMs ?? 30_000;

    return new Promise<R>((resolve, reject) => {
      let timer: ReturnType<typeof setTimeout> | undefined;

      this.pending.set(id, {
        resolve: (v) => {
          clearTimeout(timer);
          resolve(v as R);
        },
        reject: (e) => {
          clearTimeout(timer);
          reject(e);
        },
      });

      if (timeoutMs > 0) {
        timer = setTimeout(() => {
          this.pending.delete(id);
          reject(
            new Error(
              `Kernel did not respond to ${method} (id=${id}) within ${timeoutMs}ms`,
            ),
          );
        }, timeoutMs);
      }

      try {
        this.send({ jsonrpc: "2.0", id, method, params });
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error as Error);
      }
    });
  }

  /**
   * Send session/prompt and wait for the response.
   * Uses no timeout (turns include unbounded user interaction).
   * Adds a 50ms drain after the response arrives so trailing
   * session/update chunks can fire their handlers before we return.
   */
  async promptRequest(
    sessionId: string,
    text: string,
    options: PromptRequestOptions = {},
  ): Promise<PromptResult> {
    const meta = options.clientTurnId
      ? { "mustang.agent/clientTurnId": options.clientTurnId }
      : undefined;
    const result = await this.request<PromptResult>(
      AcpMethod.sessionPrompt,
      {
        sessionId,
        prompt: [{ type: "text", text }],
        ...(meta ? { meta, _meta: meta } : {}),
      },
      { timeoutMs: 0 }, // no timeout
    );
    // Kernel sends response before trailing session/update chunks
    await new Promise((r) => setTimeout(r, 50));
    return result;
  }

  async activateSkillRequest(
    sessionId: string,
    skill: string,
    args: string,
    options: PromptRequestOptions = {},
  ): Promise<PromptResult> {
    const meta = options.clientTurnId
      ? { "mustang.agent/clientTurnId": options.clientTurnId }
      : undefined;
    const result = await this.request<PromptResult>(
      MustangMethod.sessionActivateSkill,
      {
        sessionId,
        skill,
        args,
        ...(meta ? { meta, _meta: meta } : {}),
      },
      { timeoutMs: 0 },
    );
    await new Promise((r) => setTimeout(r, 50));
    return result;
  }

  async executeShellRequest(
    sessionId: string,
    command: string,
    excludeFromContext: boolean,
  ): Promise<ExecutionResult> {
    const result = await this.request<ExecutionResult>(
      MustangMethod.sessionExecuteShell,
      { sessionId, command, excludeFromContext, shell: "auto" },
      { timeoutMs: 0 },
    );
    await new Promise((r) => setTimeout(r, 50));
    return result;
  }

  async executePythonRequest(
    sessionId: string,
    code: string,
    excludeFromContext: boolean,
  ): Promise<ExecutionResult> {
    const result = await this.request<ExecutionResult>(
      MustangMethod.sessionExecutePython,
      { sessionId, code, excludeFromContext },
      { timeoutMs: 0 },
    );
    await new Promise((r) => setTimeout(r, 50));
    return result;
  }

  /** Send a notification (no response expected). */
  notify(method: string, params: unknown): void {
    this.send({ jsonrpc: "2.0", method, params });
  }

  // ------------------------------------------------------------------
  // Event subscription
  // ------------------------------------------------------------------

  /** Subscribe to session/update notifications. Returns an unsubscribe fn. */
  onUpdate(handler: UpdateHandler): () => void {
    this.updateHandlers.add(handler);
    return () => this.updateHandlers.delete(handler);
  }

  onDisconnect(handler: DisconnectHandler): () => void {
    this.disconnectHandlers.add(handler);
    if (this.disconnected) queueMicrotask(() => handler(this.disconnected!));
    return () => this.disconnectHandlers.delete(handler);
  }

  onReconnect(handler: ReconnectHandler): () => void {
    this.reconnectHandlers.add(handler);
    return () => this.reconnectHandlers.delete(handler);
  }

  onConnectionStateChange(handler: ConnectionStateHandler): () => void {
    this.connectionStateHandlers.add(handler);
    queueMicrotask(() => handler(this.connectionState));
    return () => this.connectionStateHandlers.delete(handler);
  }

  getConnectionState(): KernelConnectionState {
    return this.connectionState;
  }

  isConnected(): boolean {
    return !this.disconnected && this.ws.readyState === WebSocket.OPEN;
  }

  /** Set the global handler for session/request_permission requests. */
  setPermissionHandler(handler: PermissionHandler): void {
    this.permissionHandler = handler;
  }

  private assertConnected(): void {
    if (this.disconnected) throw this.disconnected;
    if (this.ws.readyState !== WebSocket.OPEN) {
      throw new KernelDisconnected(`Kernel connection is not open (state=${this.ws.readyState}).`);
    }
  }

  private async ensureConnected(): Promise<void> {
    if (this.isConnected()) return;
    if (this.closing) throw new KernelDisconnected("Kernel connection closed.");
    await this.reconnect();
    this.assertConnected();
  }

  private reconnect(): Promise<void> {
    if (this.isConnected()) return Promise.resolve();
    if (this.reconnecting) return this.reconnecting;
    this.setConnectionState("connecting");
    this.reconnecting = this.reconnectLoop().finally(() => {
      this.reconnecting = undefined;
    });
    return this.reconnecting;
  }

  private async reconnectLoop(): Promise<void> {
    let delayMs = 250;
    let lastError: unknown = this.disconnected;
    for (let attempt = 1; attempt <= 20 && !this.closing; attempt++) {
      await sleep(delayMs);
      if (this.closing) break;
      try {
        const next = await AcpClient.openWebSocket(this.url, this.currentToken());
        this.ws = next;
        this.disconnected = undefined;
        this.attachWebSocket(next);
        await this.initialize();
        this.setConnectionState("connected");
        for (const handler of this.reconnectHandlers) handler();
        return;
      } catch (error) {
        lastError = error;
        delayMs = Math.min(2_000, delayMs * 2);
      }
    }
    this.setConnectionState("disconnected");
    throw lastError instanceof Error
      ? lastError
      : new KernelDisconnected("Kernel reconnect failed.");
  }

  private markDisconnected(error: KernelDisconnected): void {
    if (this.disconnected) return;
    this.disconnected = error;
    this.stopHealthTimer();
    this.rejectPending(error);
    this.setConnectionState("connecting");
    for (const handler of this.disconnectHandlers) handler(error);
    void this.reconnect().catch(() => {});
  }

  private currentToken(): string {
    return this.tokenProvider?.() ?? this.token;
  }

  private setConnectionState(state: KernelConnectionState): void {
    if (this.connectionState === state) return;
    this.connectionState = state;
    for (const handler of this.connectionStateHandlers) handler(state);
  }

  private rejectPending(error: KernelDisconnected): void {
    for (const [id, entry] of this.pending) {
      this.pending.delete(id);
      entry.reject(error);
    }
  }
}

function executionToSessionUpdate(params: { sessionId?: string; execution?: Record<string, unknown> }): SessionUpdateParams {
  const execution = params.execution ?? {};
  const phase = String(execution.phase ?? "");
  const base: SessionUpdateParams = {
    sessionId: String(params.sessionId ?? ""),
    sessionUpdate: "execution_update",
    phase,
    kind: execution.kind,
    executionId: execution.executionId,
  };
  if (phase === "chunk") {
    base.stream = execution.stream;
    base.text = execution.text;
  } else if (phase === "end") {
    base.exitCode = execution.exitCode;
    base.cancelled = execution.cancelled;
  } else {
    base.input = execution.input;
    base.shell = execution.shell;
    base.excludeFromContext = execution.excludeFromContext;
  }
  return base;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function failClosedPermissionResult(req: Pick<PermissionRequest, "options">): PermissionResult {
  const reject = req.options.find((option) => option.kind.startsWith("reject"))
    ?? req.options.find((option) => option.optionId.startsWith("reject"))
    ?? req.options.find((option) => option.optionId === "deny");
  if (reject) {
    return { outcome: { outcome: "selected", optionId: reject.optionId } };
  }
  return { outcome: { outcome: "cancelled" } };
}

// ---------------------------------------------------------------------------
// Token helpers
// ---------------------------------------------------------------------------

export function readToken(): string {
  const envToken = process.env.DEEPCLI_TOKEN ?? process.env.MUSTANG_TOKEN;
  if (envToken) return envToken;

  for (const path of tokenFileCandidates()) {
    try {
      const token = readFileSync(path, "utf-8").trim();
      if (token) return token;
    } catch {
      // Try the next candidate.
    }
  }
  throw new Error(
    `No DeepCLI auth token found. Set DEEPCLI_TOKEN or run the kernel first (token at ${DEFAULT_TOKEN_FILE}).`,
  );
}
