import { assert } from "./helpers.js";
import { cancelledResult, mapPermissionRequest, optionBySelectorLabel, selectedOptionResult } from "../src/permissions/mapper.js";
import type { PermissionRequest } from "../src/acp/client.js";

const req: PermissionRequest = {
  reqId: 1,
  sessionId: "s",
  toolCall: { toolCallId: "call-1" },
  options: [
    { optionId: "allow-custom", name: "Do it once", kind: "allow_once" },
    { optionId: "reject-custom", name: "No thanks", kind: "reject_once" },
  ],
  toolInput: { command: "echo hi" },
};

const prompt = mapPermissionRequest(req);
assert(prompt.type === "tool", "expected tool prompt");
if (prompt.type !== "tool") throw new Error("expected tool prompt");
assert(prompt.title === "call-1", "missing title should fall back to toolCallId");
assert(prompt.options[0].optionId === "allow-custom", "mapper must preserve optionId");
assert(prompt.options[0].label === "Do it once", "mapper should prefer kernel option name");
assert(
  optionBySelectorLabel(prompt, prompt.options[1].selectorLabel)?.optionId === "reject-custom",
  "selector label should map back to original optionId",
);

assert(
  selectedOptionResult("allow-custom").outcome.optionId === "allow-custom",
  "selected result should carry chosen optionId",
);
assert(cancelledResult().outcome.outcome === "cancelled", "cancel result should use nested cancelled outcome");

console.log("PASS: permission mapper");
