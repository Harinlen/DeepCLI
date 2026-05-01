// @ts-nocheck
import type { AgentMessage } from "@/compat/agent-core.js";
import type { Message } from "@/compat/ai.js";

export const SKILL_PROMPT_MESSAGE_TYPE = "skill-prompt";

export interface SkillPromptDetails {
	name?: string;
	prompt?: string;
}

export function convertToLlm(messages: AgentMessage[]): Message[] {
	return messages as Message[];
}

export function bashExecutionToText(msg: unknown): string {
	return String((msg as { output?: unknown })?.output ?? "");
}

export function pythonExecutionToText(msg: unknown): string {
	return String((msg as { output?: unknown })?.output ?? "");
}
