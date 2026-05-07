import type { AcpSessionInfo, CliSessionInfo } from "@/sessions/types.js";

export function mapAcpSessionInfo(raw: AcpSessionInfo): CliSessionInfo {
  const sessionId = String(raw.sessionId ?? raw.id ?? "");
  const metadata = raw._meta ?? raw.meta;
  const sessionMeta = sessionMetadata(metadata);
  const createdAt = stringOrNull(raw.createdAt ?? metadata?.createdAt);
  const updatedAt = stringOrNull(raw.updatedAt ?? metadata?.updatedAt ?? createdAt);
  const cwd = stringOrNull(raw.cwd) ?? "";
  const title = displayTitle(raw.title) ?? fallbackTitle(sessionId, cwd);

  return {
    sessionId,
    path: sessionId,
    title,
    cwd,
    updatedAt,
    createdAt,
    archivedAt: stringOrNull(raw.archivedAt ?? sessionMeta.archivedAt),
    titleSource: stringOrNull(raw.titleSource ?? sessionMeta.titleSource),
    totalInputTokens: numberOrNull(metadata?.totalInputTokens),
    totalOutputTokens: numberOrNull(metadata?.totalOutputTokens),
    raw,
  };
}

function sessionMetadata(metadata: AcpSessionInfo["_meta"] | AcpSessionInfo["meta"]): Record<string, unknown> {
  const value = metadata?.["mustang.agent/session"];
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function displayTitle(value: unknown): string | null {
  if (typeof value !== "string" || value.length === 0) return null;
  const withoutAnsi = value.replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, " ");
  const withoutClosedReminder = withoutAnsi.replace(/<system-reminder\b[^>]*>[\s\S]*?<\/system-reminder>/g, " ");
  const withoutReminder = withoutClosedReminder.replace(/<system-reminder\b[^>]*>[\s\S]*$/g, " ");
  const withoutControls = withoutReminder.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, " ");
  const collapsed = withoutControls.split(/\s+/).filter(Boolean).join(" ");
  return collapsed.length > 0 ? collapsed : null;
}

function fallbackTitle(sessionId: string, cwd: string): string {
  if (cwd) return cwd.split(/[\\/]/).filter(Boolean).at(-1) ?? cwd;
  if (sessionId) return `Session ${sessionId.slice(0, 8)}`;
  return "Untitled session";
}
