export interface AcpSessionInfo {
  sessionId?: string;
  id?: string;
  title?: string | null;
  cwd?: string | null;
  updatedAt?: string | null;
  createdAt?: string | null;
  archivedAt?: string | null;
  titleSource?: string | null;
  messageCount?: number | null;
  message_count?: number | null;
  turnCount?: number | null;
  turn_count?: number | null;
  _meta?: {
    createdAt?: string | null;
    updatedAt?: string | null;
    totalInputTokens?: number | null;
    totalOutputTokens?: number | null;
    "mustang.agent/session"?: {
      archivedAt?: string | null;
      titleSource?: string | null;
      [key: string]: unknown;
    } | null;
    [key: string]: unknown;
  } | null;
  meta?: {
    createdAt?: string | null;
    updatedAt?: string | null;
    totalInputTokens?: number | null;
    totalOutputTokens?: number | null;
    "mustang.agent/session"?: {
      archivedAt?: string | null;
      titleSource?: string | null;
      [key: string]: unknown;
    } | null;
    [key: string]: unknown;
  } | null;
  [key: string]: unknown;
}

export interface CliSessionInfo {
  sessionId: string;
  path: string;
  title: string;
  cwd: string;
  updatedAt: string | null;
  createdAt: string | null;
  archivedAt: string | null;
  titleSource: string | null;
  totalInputTokens: number | null;
  totalOutputTokens: number | null;
  messageCount: number | null;
  turnCount: number | null;
  raw: AcpSessionInfo;
}

export interface ListSessionsOptions {
  cwd?: string;
  includeArchived?: boolean;
  archivedOnly?: boolean;
  limit?: number;
}

export interface SessionListResponse {
  sessions?: AcpSessionInfo[];
  nextCursor?: string | null;
}

export interface SessionCreateResponse {
  sessionId: string;
  configOptions?: unknown[];
  modes?: unknown[];
}

export interface SessionLoadResponse {
  sessionId?: string;
  session?: AcpSessionInfo;
  configOptions?: unknown[];
  modes?: unknown[];
}

export interface SessionServiceClient {
  request<R = unknown>(method: string, params: unknown, opts?: { timeoutMs?: number }): Promise<R>;
}
