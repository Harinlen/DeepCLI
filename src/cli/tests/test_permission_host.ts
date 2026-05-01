import { assert } from "./helpers.js";
import type { PermissionRequest } from "../src/acp/client.js";
import { PermissionController, type HookPromptHost } from "../src/permissions/controller.js";

const calls: string[] = [];

const host: HookPromptHost = {
  async showHookSelector(title, options, dialogOptions) {
    calls.push(`selector:${title.split("\n")[0]}:${dialogOptions?.outline === true ? "outline" : "plain"}`);
    return options[1];
  },
  async showHookInput(title, placeholder) {
    calls.push(`input:${title.split("\n")[0]}:${placeholder ?? ""}`);
    return "typed answer";
  },
  async showHookEditor(title, prefill, _dialogOptions, editorOptions) {
    calls.push(`editor:${title.split("\n")[0]}:${prefill ?? ""}:${editorOptions?.promptStyle === true ? "prompt" : "plain"}`);
    return "long answer";
  },
};

const controller = new PermissionController(host);

const toolReq: PermissionRequest = {
  reqId: 1,
  sessionId: "s",
  toolCall: { toolCallId: "call-1", title: "Bash" },
  options: [
    { optionId: "allow-once", name: "Allow once", kind: "allow_once" },
    { optionId: "reject-once", name: "Reject", kind: "reject_once" },
  ],
  toolInput: { command: "echo hi" },
};

const toolResult = await controller.handleRequest(toolReq);
assert(toolResult.outcome.outcome === "selected", "tool permission should select through hook host");
assert(toolResult.outcome.optionId === "reject-once", "selected hook label must map back to original optionId");
assert(calls[0] === "selector:Tool Authorization:outline", "tool prompt should use OMP hook selector with outline");

const textReq: PermissionRequest = {
  reqId: 2,
  sessionId: "s",
  toolCall: { toolCallId: "ask", title: "AskUserQuestion" },
  options: [{ optionId: "allow-once", name: "Answer", kind: "allow_once" }],
  toolInput: {
    questions: [
      { type: "text", header: "Name", question: "What name?", placeholder: "Type it" },
      { type: "text", header: "Bio", question: "Long answer?", multiline: true },
    ],
  },
};

const textResult = await controller.handleRequest(textReq);
assert(textResult.outcome.outcome === "selected", "question permission should select allow option");
assert(textResult.outcome.updatedInput?.answers["What name?"] === "typed answer", "single-line questions should use hook input");
assert(textResult.outcome.updatedInput?.answers["Long answer?"] === "long answer", "multiline questions should use hook editor");
assert(calls.includes("input:**Name**:Type it"), "text question should use OMP hook input");
assert(calls.includes("editor:**Bio**::prompt"), "multiline question should use OMP hook editor");

console.log("PASS: permission hook host");
