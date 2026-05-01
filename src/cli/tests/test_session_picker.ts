import { createPickerState, filterSessions, movePickerSelection, pickSessionByNumber, renderSessionPicker } from "../src/sessions/picker.js";
import type { CliSessionInfo } from "../src/sessions/types.js";
import { assert } from "./helpers.js";

const sessions = [
  session("s1", "Alpha Project", "/repo/alpha"),
  session("s2", "Beta", "/repo/beta", "2026-04-28T00:00:00Z"),
  session("s3", "Gamma", "/repo/gamma"),
];

const state = createPickerState(sessions, 2);
state.query = "alpr";
assert(filterSessions(state).length === 1, "picker should fuzzy filter sessions");
assert(filterSessions(state)[0].sessionId === "s1", "picker fuzzy result should match title/cwd");
state.query = "";
movePickerSelection(state, 10);
assert(state.selected === 2, "picker selection should clamp to list end");
const rendered = renderSessionPicker(state);
assert(rendered.includes("Gamma"), "picker render should include selected page");
assert(renderSessionPicker(createPickerState([])).includes("No sessions"), "empty picker should render new-session hint");
assert(pickSessionByNumber(sessions, "2") === sessions[1], "number picker should select session");
assert(pickSessionByNumber(sessions, "new") === "new", "new picker command should return new");
assert(pickSessionByNumber(sessions, "q") === "cancel", "cancel picker command should return cancel");

console.log("PASS: session picker model");

function session(sessionId: string, title: string, cwd: string, archivedAt: string | null = null): CliSessionInfo {
  return {
    sessionId,
    path: sessionId,
    title,
    cwd,
    updatedAt: null,
    createdAt: null,
    archivedAt,
    titleSource: null,
    totalInputTokens: null,
    totalOutputTokens: null,
    raw: { sessionId, title, cwd, archivedAt },
  };
}
