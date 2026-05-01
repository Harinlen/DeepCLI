import type { PermissionRequest, PermissionResult } from "@/acp/client.js";

export type ToolPermissionOptionKind =
  | "allow_once"
  | "allow_always"
  | "reject_once"
  | "reject_always"
  | string;

export interface ToolPermissionOption {
  optionId: string;
  label: string;
  kind: ToolPermissionOptionKind;
  selectorLabel: string;
}

export interface ToolPermissionPrompt {
  type: "tool";
  title: string;
  body: string;
  options: ToolPermissionOption[];
}

export type StructuredQuestionKind = "choice" | "text";

export interface StructuredQuestion {
  kind: StructuredQuestionKind;
  header: string;
  question: string;
  options: string[];
  placeholder?: string;
  multiline: boolean;
  maxLength?: number;
  multiple: boolean;
}

export interface StructuredQuestionPrompt {
  type: "questions";
  title: string;
  questions: StructuredQuestion[];
  originalInput: Record<string, unknown>;
  allowOptionId: string;
}

export type PermissionPrompt = ToolPermissionPrompt | StructuredQuestionPrompt;
export type PermissionDecision = PermissionResult;
export type PermissionRequestHandler = (req: PermissionRequest) => Promise<PermissionDecision>;
