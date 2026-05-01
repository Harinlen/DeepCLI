import type { PermissionRequest, PermissionResult } from "@/acp/client.js";
import type {
  PermissionPrompt,
  StructuredQuestion,
  StructuredQuestionPrompt,
  ToolPermissionOption,
  ToolPermissionPrompt,
} from "./types.js";

type RawQuestion = Record<string, unknown>;

export function mapPermissionRequest(req: PermissionRequest): PermissionPrompt {
  const questions = extractQuestions(req.toolInput);
  if (questions.length > 0 || looksLikeAskUserQuestion(req)) {
    return mapQuestionPrompt(req, questions);
  }
  return mapToolPrompt(req);
}

export function selectedOptionResult(optionId: string): PermissionResult {
  return { outcome: { outcome: "selected", optionId } };
}

export function cancelledResult(): PermissionResult {
  return { outcome: { outcome: "cancelled" } };
}

export function questionsResult(prompt: StructuredQuestionPrompt, answers: Record<string, string>): PermissionResult {
  return {
    outcome: {
      outcome: "selected",
      optionId: prompt.allowOptionId,
      updatedInput: {
        ...prompt.originalInput,
        questions: prompt.originalInput.questions,
        answers,
      },
    },
  };
}

function mapToolPrompt(req: PermissionRequest): ToolPermissionPrompt {
  const title = req.toolCall.title || req.toolCall.toolCallId || "Tool Authorization";
  const summary = req.toolCall.inputSummary ? `${req.toolCall.inputSummary}\n\n` : "";
  const input = req.toolInput ? `\`\`\`json\n${JSON.stringify(req.toolInput, null, 2)}\n\`\`\`` : "";
  const options = req.options.map((option, index, all) => {
    const label = option.name || labelForKind(option.kind) || option.optionId;
    return {
      optionId: option.optionId,
      label,
      kind: option.kind,
      selectorLabel: uniqueSelectorLabel(label, option.optionId, index, all),
    };
  });
  return {
    type: "tool",
    title,
    body: `Tool Authorization\n\n**${title}**\n\n${summary}${input}`.trim(),
    options,
  };
}

function mapQuestionPrompt(req: PermissionRequest, rawQuestions: RawQuestion[]): StructuredQuestionPrompt {
  const allowOption = req.options.find((option) => option.kind.startsWith("allow"))
    ?? req.options.find((option) => option.optionId.startsWith("allow"))
    ?? req.options[0];
  return {
    type: "questions",
    title: req.toolCall.title || "Question",
    questions: rawQuestions.map(mapQuestion),
    originalInput: req.toolInput ?? { questions: rawQuestions },
    allowOptionId: allowOption?.optionId ?? "allow_once",
  };
}

function mapQuestion(raw: RawQuestion): StructuredQuestion {
  const kind = raw.type === "text" ? "text" : "choice";
  const options = Array.isArray(raw.options) ? raw.options.map(formatQuestionOption) : [];
  return {
    kind,
    header: String(raw.header ?? "Question"),
    question: String(raw.question ?? ""),
    options,
    placeholder: typeof raw.placeholder === "string" ? raw.placeholder : undefined,
    multiline: raw.multiline === true,
    maxLength: typeof raw.maxLength === "number" && Number.isFinite(raw.maxLength) ? raw.maxLength : undefined,
    multiple: raw.multiple === true || raw.multiselect === true,
  };
}

function formatQuestionOption(raw: unknown): string {
  if (raw !== null && typeof raw === "object" && !Array.isArray(raw)) {
    const option = raw as Record<string, unknown>;
    const label = option.label ?? option.name ?? option.value ?? option.id;
    if (label !== undefined) return String(label);
  }
  return String(raw);
}

function extractQuestions(toolInput: Record<string, unknown> | undefined): RawQuestion[] {
  const raw = toolInput?.questions;
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is RawQuestion => item !== null && typeof item === "object" && !Array.isArray(item));
}

function looksLikeAskUserQuestion(req: PermissionRequest): boolean {
  const text = `${req.toolCall.title ?? ""} ${req.toolCall.inputSummary ?? ""}`.toLowerCase();
  return text.includes("askuserquestion") || text.includes("ask user question");
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

function uniqueSelectorLabel(
  label: string,
  optionId: string,
  index: number,
  all: PermissionRequest["options"],
): string {
  const duplicate = all.some((option, otherIndex) => otherIndex !== index && (option.name || labelForKind(option.kind) || option.optionId) === label);
  return duplicate ? `${label} (${optionId})` : label;
}

export function optionBySelectorLabel(
  prompt: ToolPermissionPrompt,
  selectorLabel: string | undefined,
): ToolPermissionOption | undefined {
  return prompt.options.find((option) => option.selectorLabel === selectorLabel);
}
