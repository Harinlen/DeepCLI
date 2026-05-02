export type Effort = "minimal" | "low" | "medium" | "high" | "xhigh";
export const THINKING_EFFORTS: Effort[] = ["minimal", "low", "medium", "high", "xhigh"];
export const Effort = {
	Minimal: "minimal",
	Low: "low",
	Medium: "medium",
	High: "high",
	XHigh: "xhigh",
} as const;

export interface Usage { input?: number; output?: number; cacheRead?: number; cacheWrite?: number; [key: string]: unknown }
export interface UsageReport extends Usage { total?: number }
export interface ImageContent { type: "image"; data?: string | Uint8Array; mimeType: string }
export interface TextContent { type: "text"; text: string }
export interface ToolCall { id?: string; name?: string; input?: unknown }
export interface ToolResultMessage { role?: "tool"; content?: unknown }
export interface Message { role?: string; content?: unknown; [key: string]: unknown }
export interface MessageAttribution { source?: string }
export interface ProviderSessionState { [key: string]: unknown }
export type ServiceTier = string;
export type ToolChoice = unknown;
export type Context = Record<string, unknown>;
export type OAuthCredentials = Record<string, unknown>;
export type OAuthLoginCallbacks = Record<string, unknown>;
export type SimpleStreamOptions = Record<string, unknown>;
export type AssistantMessageEvent = Record<string, unknown>;
export type AssistantMessageEventStream = AsyncIterable<AssistantMessageEvent>;

export type AssistantContent =
	| TextContent
	| { type: "thinking"; thinking: string }
	| { type: "toolCall"; toolCallId?: string; name?: string; input?: unknown }
	| ImageContent;

export interface AssistantMessage extends Message {
	role?: "assistant";
	content: AssistantContent[];
	stopReason?: string;
	errorMessage?: string;
	usage?: Usage;
}

export interface Model { id: string; name?: string; provider?: string }
export interface Api { id?: string; name?: string }
export type Provider = string;
export interface ProviderDetails {
	provider: string;
	fields: Array<{ label: string; value: string }>;
}

export const StringEnum = <T extends string[]>(values: T): T => values;

export function modelsAreEqual(a?: Model | null, b?: Model | null): boolean {
	return a?.id === b?.id;
}

export function getSupportedEfforts(): Effort[] {
	return THINKING_EFFORTS;
}

export function clampThinkingLevelForModel(_model: unknown, level: Effort): Effort {
	return level;
}

export function isContextOverflow(_error: unknown): boolean { return false; }
export function isUsageLimitError(_error: unknown): boolean { return false; }
export function calculateRateLimitBackoffMs(): number { return 0; }
export function parseRateLimitReason(): string | undefined { return undefined; }
export async function completeSimple(): Promise<string> { return ""; }
export function getEnvApiKey(name?: string): string | undefined { return name ? process.env[name] : undefined; }
export function getProviderDetails(input?: string | { model?: Model; sessionId?: string }): ProviderDetails {
	const model = typeof input === "object" ? input.model : undefined;
	const provider = model?.provider ?? (typeof input === "string" ? input : undefined) ?? "provider";
	const fields: Array<{ label: string; value: string }> = [];
	if (model?.name) fields.push({ label: "Model", value: model.name });
	if (model?.id && model.id !== model.name) fields.push({ label: "Model ID", value: model.id });
	if (typeof input === "object" && input.sessionId) fields.push({ label: "Session", value: input.sessionId });
	return { provider, fields };
}
export function getOAuthProviders(): unknown[] { return []; }
