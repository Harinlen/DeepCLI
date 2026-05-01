export enum ThinkingLevel {
	Off = "off",
	Minimal = "minimal",
	Low = "low",
	Medium = "medium",
	High = "high",
	XHigh = "xhigh",
}

export interface Agent {
	id?: string;
	name?: string;
	model?: { id?: string; name?: string } | string;
	provider?: string;
	messages?: AgentMessage[];
	[key: string]: unknown;
}

export interface AgentState { [key: string]: unknown }
export interface AgentEvent { type: string; [key: string]: unknown }
export interface AgentMessage { role?: string; content?: unknown; [key: string]: unknown }
export interface AgentTool<TInput = unknown, TResult = unknown> {
	id?: string;
	name: string;
	title?: string;
	status?: string;
	input?: TInput;
	output?: TResult;
	error?: string;
	[key: string]: unknown;
}

export interface AgentToolContext { [key: string]: unknown }
export type AgentToolResult = unknown;
export type AgentToolExecFn = (...args: unknown[]) => Promise<AgentToolResult> | AgentToolResult;
export type AgentToolUpdateCallback = (...args: unknown[]) => void;
export interface ToolCallContext { [key: string]: unknown }
export const INTENT_FIELD = "__intent";

export class AgentBusyError extends Error {}
