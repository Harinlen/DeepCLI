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
assert(prompt.title === "Bash command", "command tools should use a structured permission title");
assert(prompt.body.startsWith("Bash command"), "tool prompt should start with the display title");
assert(prompt.body.includes("Command: `echo hi`"), "command input should render as a compact code field");
assert(!prompt.body.includes("```json"), "tool prompt should not fall back to a JSON code block");
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

const webReq: PermissionRequest = {
  reqId: 2,
  sessionId: "s",
  toolCall: {
    toolCallId: "call-web",
    title: "WebFetch",
    inputSummary: "WebFetch: Fetching www.weather25.com (outbound fetch to www.weather25.com)",
  },
  options: [
    { optionId: "allow_once", name: "Allow once", kind: "allow_once" },
    { optionId: "allow_always", name: "Allow always", kind: "allow_always" },
    { optionId: "reject", name: "Deny", kind: "reject_once" },
  ],
  toolInput: {
    url: "https://www.weather25.com/oceania/australia/new-south-wales/sydney?page=today",
    prompt: "Extract the current weather for Sydney today: temperature, conditions, humidity, wind, and forecast.",
  },
};

const webPrompt = mapPermissionRequest(webReq);
assert(webPrompt.type === "tool", "expected web tool prompt");
if (webPrompt.type !== "tool") throw new Error("expected web tool prompt");
assert(webPrompt.title === "Fetch", "WebFetch should use Claude Code-style Fetch title");
assert(webPrompt.body.includes("Domain: `www.weather25.com`"), "WebFetch should expose the domain");
assert(webPrompt.body.includes("URL: `https://www.weather25.com/"), "URL should be shown as inline code");
assert(webPrompt.body.includes("Instruction: Extract the current weather"), "prompt should be shown as an instruction");
assert(
  webPrompt.body.includes("Reason: WebFetch: Fetching `www.weather25.com`"),
  "reason text should prevent markdown autolinks",
);
assert(!webPrompt.body.includes("[www.weather25.com]"), "WebFetch body should not synthesize markdown links");
assert(!webPrompt.body.includes("```json"), "WebFetch body should not render raw JSON");
assert(
  webPrompt.options.some((option) => option.label === "Allow always for www.weather25.com"),
  "WebFetch allow-always option should name the domain",
);

const mcpReq: PermissionRequest = {
  reqId: 3,
  sessionId: "s",
  toolCall: { toolCallId: "call-mcp", title: "weather/getForecast" },
  options: [{ optionId: "allow_once", name: "Allow once", kind: "allow_once" }],
  toolInput: {
    endpoint: "https://api.example.test/forecast?city=Sydney",
    query: "today with humidity and wind",
    units: "metric",
  },
};

const mcpPrompt = mapPermissionRequest(mcpReq);
assert(mcpPrompt.type === "tool", "expected generic tool prompt");
if (mcpPrompt.type !== "tool") throw new Error("expected generic tool prompt");
assert(mcpPrompt.body.includes("Tool: weather/getForecast"), "generic renderer should show the tool name");
assert(mcpPrompt.body.includes("Resource: `https://api.example.test/forecast"), "generic URL-like fields should be code");
assert(mcpPrompt.body.includes("Instruction: today with humidity and wind"), "generic instruction fields should be promoted");
assert(mcpPrompt.body.includes("Other parameters: units: metric"), "unrecognized fields should remain visible");

console.log("PASS: permission mapper");
