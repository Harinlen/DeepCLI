import { assert } from "./helpers.js";
import { failClosedPermissionResult, type PermissionRequest } from "../src/acp/client.js";

const req: PermissionRequest = {
  reqId: 3,
  sessionId: "s",
  toolCall: { toolCallId: "call" },
  options: [
    { optionId: "allow_once", name: "Allow once", kind: "allow_once" },
    { optionId: "reject_once", name: "Reject", kind: "reject_once" },
  ],
};

const result = failClosedPermissionResult(req);

assert(result.outcome.outcome === "selected", "fail-closed should use reject option when present");
assert(result.outcome.optionId === "reject_once", "fail-closed must not silently allow once");

console.log("PASS: permission fail-closed default");
