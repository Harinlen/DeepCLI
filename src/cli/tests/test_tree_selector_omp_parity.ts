const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { initTheme } = await load("../src/active-port/coding-agent/modes/theme/theme.ts");
const { TreeSelectorComponent } = await load("../src/active-port/coding-agent/modes/components/tree-selector.ts");
import { assert } from "./helpers.js";

await initTheme(false, "unicode", false, "dark", "dark");

const selected: string[] = [];
const cancelled: string[] = [];
const labels: string[] = [];
const selector = new TreeSelectorComponent(
	[
		node("root", "user", "Root request", [
			node("assistant-1", "assistant", "First response", [
				node("user-2", "user", "Follow up"),
				toolNode("tool-1"),
				node("assistant-2", "assistant", "Second response"),
			]),
		]),
	],
	"assistant-2",
	20,
	id => selected.push(id),
	() => cancelled.push("cancel"),
	(id, label) => labels.push(`${id}:${label ?? ""}`),
);

let frame = Bun.stripANSI(selector.render(100).join("\n"));
assert(frame.includes("Session Tree"), "tree selector should render the session tree heading");
assert(frame.includes("Second response"), "tree selector should render session entries");

selector.handleInput("\x1b[D");
selector.handleInput("\x1b[C");
selector.handleInput("\r");
assert(selected[0] === "assistant-2", "Left/Right page navigation should preserve valid selection and Enter should select");

selector.handleInput("\x0f");
frame = Bun.stripANSI(selector.render(100).join("\n"));
assert(frame.includes("[no-tools]"), "Ctrl+O should cycle tree filter forward");
selector.handleInput("\x1b[27;6;111~");
frame = Bun.stripANSI(selector.render(100).join("\n"));
assert(!frame.includes("[no-tools]"), "Shift+Ctrl+O should cycle tree filter backward");
selector.handleInput("\x1bt");
frame = Bun.stripANSI(selector.render(100).join("\n"));
assert(frame.includes("[no-tools]"), "Alt+T should switch directly to no-tools filter");
selector.handleInput("\x1bu");
frame = Bun.stripANSI(selector.render(100).join("\n"));
assert(frame.includes("[user]"), "Alt+U should switch directly to user-only filter");
selector.handleInput("\x1ba");
frame = Bun.stripANSI(selector.render(100).join("\n"));
assert(frame.includes("[all]"), "Alt+A should switch directly to all filter");

selector.handleInput("F");
selector.handleInput("o");
selector.handleInput("l");
selector.handleInput("l");
frame = Bun.stripANSI(selector.render(100).join("\n"));
assert(frame.includes("Search: Foll"), "typing should update tree search query");
selector.handleInput("\x1b");
frame = Bun.stripANSI(selector.render(100).join("\n"));
assert(frame.includes("Search:"), "Escape should keep search line visible");
assert(!frame.includes("Search: Foll"), "Escape should clear tree search before cancelling");
selector.handleInput("\x1b");
assert(cancelled.includes("cancel"), "Escape with empty search should cancel tree selector");

const labelSelector = new TreeSelectorComponent(
	[node("label-root", "user", "Needs label")],
	"label-root",
	20,
	() => {},
	() => {},
	(id, label) => labels.push(`${id}:${label ?? ""}`),
);
labelSelector.handleInput("\x1b[76;2u");
frame = Bun.stripANSI(labelSelector.render(100).join("\n"));
assert(frame.includes("Label (empty to remove):"), "Shift+L should open label editor");
labelSelector.handleInput("n");
labelSelector.handleInput("o");
labelSelector.handleInput("t");
labelSelector.handleInput("e");
labelSelector.handleInput("\r");
assert(labels.includes("label-root:note"), "label editor should submit label changes");

console.log("PASS: tree selector OMP parity");

function node(id: string, role: string, text: string, children: unknown[] = []) {
	return {
		entry: {
			id,
			parentId: null,
			type: "message",
			message: {
				role,
				content: [{ type: "text", text }],
			},
		},
		children,
	};
}

function toolNode(id: string) {
	return {
		entry: {
			id,
			parentId: null,
			type: "message",
			message: {
				role: "toolResult",
				content: [{ type: "text", text: "tool output" }],
			},
		},
		children: [],
	};
}
