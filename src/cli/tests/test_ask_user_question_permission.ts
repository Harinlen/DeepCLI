import { assert } from "./helpers.js";
import { mapPermissionRequest, questionsResult } from "../src/permissions/mapper.js";
import type { PermissionRequest } from "../src/acp/client.js";

const req: PermissionRequest = {
  reqId: 2,
  sessionId: "s",
  toolCall: { toolCallId: "ask-1", title: "AskUserQuestion" },
  options: [{ optionId: "allow_once", name: "Allow once", kind: "allow_once" }],
  toolInput: {
    questions: [
      { header: "Project", question: "Which project?", options: [{ label: "DeepCLI", description: "This repo" }, "Other"] },
      { type: "text", header: "Name", question: "Project name?", placeholder: "name", maxLength: 8 },
    ],
  },
};

const prompt = mapPermissionRequest(req);
assert(prompt.type === "questions", "AskUserQuestion request should map to structured questions");
if (prompt.type !== "questions") throw new Error("expected structured questions prompt");
assert(prompt.questions.length === 2, "expected two questions");
assert(prompt.questions[0].kind === "choice", "missing type should default to choice");
assert(prompt.questions[0].options[0] === "DeepCLI", "object options should render by label");
assert(prompt.questions[1].kind === "text", "text question should map to input prompt");
assert(prompt.questions[1].maxLength === 8, "maxLength should be preserved");

const result = questionsResult(prompt, {
  "Which project?": "DeepCLI",
  "Project name?": "truenorth",
});

assert(result.outcome.outcome === "selected", "question result should select an allow option");
assert(result.outcome.optionId === "allow_once", "question result should use allow option");
assert(Boolean(result.outcome.updatedInput), "question result should include updatedInput");
assert(
  (result.outcome.updatedInput?.answers as Record<string, string>)["Project name?"] === "truenorth",
  "updatedInput.answers should carry text answer",
);
assert(Array.isArray(result.outcome.updatedInput?.questions), "updatedInput should preserve original questions");

console.log("PASS: AskUserQuestion permission mapping");
