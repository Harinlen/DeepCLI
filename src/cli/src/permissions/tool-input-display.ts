import type { PermissionRequest } from "@/acp/client.js";
import type { ToolPermissionSection } from "./types.js";

type DisplayKind = "web_fetch" | "bash" | "file" | "generic";

interface ToolDisplay {
  title: string;
  sections: ToolPermissionSection[];
  optionContext?: string;
}

const RESOURCE_KEYS = new Set(["url", "uri", "endpoint", "host", "domain"]);
const FILE_KEYS = new Set(["path", "file", "file_path", "filepath", "directory"]);
const COMMAND_KEYS = new Set(["command", "script", "cmd"]);
const INSTRUCTION_KEYS = new Set(["prompt", "query", "instruction", "instructions"]);
const CONTENT_KEYS = new Set(["content", "body", "text", "message"]);

export function buildToolDisplay(req: PermissionRequest): ToolDisplay {
  const title = req.toolCall.title || req.toolCall.toolCallId || "Tool Authorization";
  const input = req.toolInput ?? {};
  const kind = classifyTool(title, input);
  if (kind === "web_fetch") return webFetchDisplay(input, req.toolCall.inputSummary);
  if (kind === "bash") return bashDisplay(input, req.toolCall.inputSummary);
  if (kind === "file") return fileDisplay(title, input, req.toolCall.inputSummary);
  return genericDisplay(title, input, req.toolCall.inputSummary);
}

export function formatPermissionBody(display: ToolDisplay): string {
  const lines: string[] = [display.title];
  for (const section of display.sections) {
    if (!section.value.trim()) continue;
    const value = section.style === "code" ? codeSpan(section.value) : escapeAutolinks(section.value);
    if (section.style === "code") {
      lines.push(`${section.label}: ${value}`);
    } else if (section.value.includes("\n")) {
      lines.push(`${section.label}:`);
      lines.push(value);
    } else {
      lines.push(`${section.label}: ${value}`);
    }
  }
  return lines.join("\n").trim();
}

export function displayOptionLabel(
  option: PermissionRequest["options"][number],
  display: ToolDisplay,
): string {
  const base = option.name || labelForKind(option.kind) || option.optionId;
  if (option.kind !== "allow_always" || !display.optionContext) return base;
  if (/always/i.test(base)) return `${base} for ${display.optionContext}`;
  return base;
}

function classifyTool(title: string, input: Record<string, unknown>): DisplayKind {
  const normalizedTitle = title.toLowerCase();
  if (normalizedTitle.includes("webfetch") || normalizedTitle.includes("web fetch")) return "web_fetch";
  if (normalizedTitle.includes("bash") || normalizedTitle.includes("powershell")) return "bash";
  if (
    normalizedTitle.includes("file")
    || Object.keys(input).some((key) => FILE_KEYS.has(key.toLowerCase()))
  ) {
    return "file";
  }
  if (typeof input.url === "string") return "web_fetch";
  if (typeof input.command === "string") return "bash";
  return "generic";
}

function webFetchDisplay(
  input: Record<string, unknown>,
  summary: string | undefined,
): ToolDisplay {
  const url = stringValue(input.url);
  const host = hostFromUrl(url);
  const sections: ToolPermissionSection[] = [];
  if (host) sections.push({ label: "Domain", value: host, style: "text" });
  if (url) sections.push({ label: "URL", value: url, style: "code" });
  const prompt = stringValue(input.prompt);
  if (prompt) sections.push({ label: "Instruction", value: prompt, style: "multiline" });
  addReason(sections, summary);
  addOtherParameters(sections, input, new Set(["url", "prompt"]));
  return { title: "Fetch", sections, optionContext: host || undefined };
}

function bashDisplay(
  input: Record<string, unknown>,
  summary: string | undefined,
): ToolDisplay {
  const command = stringValue(input.command) || stringValue(input.script) || stringValue(input.cmd);
  const sections: ToolPermissionSection[] = [];
  if (command) sections.push({ label: "Command", value: command, style: "code" });
  const description = stringValue(input.description);
  if (description) sections.push({ label: "Description", value: description, style: "text" });
  addReason(sections, summary);
  addOtherParameters(sections, input, new Set(["command", "script", "cmd", "description"]));
  return { title: "Bash command", sections };
}

function fileDisplay(
  title: string,
  input: Record<string, unknown>,
  summary: string | undefined,
): ToolDisplay {
  const path = firstString(input, ["file_path", "filepath", "path", "file", "directory"]);
  const content = firstString(input, ["content", "body", "text"]);
  const sections: ToolPermissionSection[] = [];
  if (path) sections.push({ label: "File", value: path, style: "code" });
  if (content) sections.push({ label: "Content preview", value: preview(content), style: "multiline" });
  addReason(sections, summary);
  addOtherParameters(
    sections,
    input,
    new Set(["file_path", "filepath", "path", "file", "directory", "content", "body", "text"]),
  );
  return { title: title.includes("/") ? "File operation" : title, sections };
}

function genericDisplay(
  title: string,
  input: Record<string, unknown>,
  summary: string | undefined,
): ToolDisplay {
  const sections: ToolPermissionSection[] = [];
  addRecognizedInputSections(sections, input);
  addReason(sections, summary);
  addOtherParameters(sections, input, recognizedKeys(input));
  if (sections.length === 0) {
    sections.push({ label: "Parameters", value: "(none)", style: "text" });
  }
  return {
    title: "Tool Authorization",
    sections: [{ label: "Tool", value: title, style: "text" }, ...sections],
  };
}

function addRecognizedInputSections(
  sections: ToolPermissionSection[],
  input: Record<string, unknown>,
): void {
  for (const [key, value] of Object.entries(input)) {
    const lower = key.toLowerCase();
    const text = stringValue(value);
    if (!text) continue;
    if (RESOURCE_KEYS.has(lower)) {
      sections.push({ label: labelForKey(key, "Resource"), value: text, style: "code" });
    } else if (FILE_KEYS.has(lower)) {
      sections.push({ label: labelForKey(key, "File"), value: text, style: "code" });
    } else if (COMMAND_KEYS.has(lower)) {
      sections.push({ label: labelForKey(key, "Command"), value: text, style: "code" });
    } else if (INSTRUCTION_KEYS.has(lower)) {
      sections.push({ label: labelForKey(key, "Instruction"), value: text, style: "multiline" });
    } else if (CONTENT_KEYS.has(lower)) {
      sections.push({
        label: labelForKey(key, "Content preview"),
        value: preview(text),
        style: "multiline",
      });
    }
  }
}

function addReason(sections: ToolPermissionSection[], summary: string | undefined): void {
  const cleaned = summary?.trim();
  if (!cleaned) return;
  sections.push({ label: "Reason", value: cleaned, style: "text" });
}

function addOtherParameters(
  sections: ToolPermissionSection[],
  input: Record<string, unknown>,
  consumed: Set<string>,
): void {
  const rows = Object.entries(input)
    .filter(([key]) => !consumed.has(key) && !consumed.has(key.toLowerCase()))
    .map(([key, value]) => `${key}: ${formatValue(value)}`);
  if (rows.length > 0) {
    sections.push({ label: "Other parameters", value: rows.join("\n"), style: "parameters" });
  }
}

function recognizedKeys(input: Record<string, unknown>): Set<string> {
  const keys = new Set<string>();
  for (const key of Object.keys(input)) {
    const lower = key.toLowerCase();
    if (
      RESOURCE_KEYS.has(lower)
      || FILE_KEYS.has(lower)
      || COMMAND_KEYS.has(lower)
      || INSTRUCTION_KEYS.has(lower)
      || CONTENT_KEYS.has(lower)
    ) {
      keys.add(key);
      keys.add(lower);
    }
  }
  return keys;
}

function firstString(input: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = stringValue(input[key]);
    if (value) return value;
  }
  return "";
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function formatValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value === null) return "null";
  return JSON.stringify(value);
}

function preview(text: string): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized.length <= 320) return normalized;
  return `${normalized.slice(0, 317)}...`;
}

function hostFromUrl(url: string): string {
  if (!url) return "";
  try {
    const parsed = new URL(url.includes("://") ? url : `https://${url}`);
    return parsed.hostname;
  } catch {
    return "";
  }
}

function codeSpan(value: string): string {
  return `\`${value.replace(/`/g, "\\`")}\``;
}

function escapeAutolinks(value: string): string {
  return value.replace(
    /\b(?:https?:\/\/[^\s`]+|www\.[^\s`)]+)/g,
    (match) => codeSpan(match),
  );
}

function labelForKey(key: string, fallback: string): string {
  return key === key.toLowerCase() ? fallback : key;
}

function labelForKind(kind: string): string {
  switch (kind) {
    case "allow_once":
      return "Allow once";
    case "allow_always":
      return "Allow always";
    case "reject_once":
      return "Reject";
    case "reject_always":
      return "Reject always";
    default:
      return "";
  }
}
