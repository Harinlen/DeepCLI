const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { initTheme } = await load("../src/active-port/coding-agent/modes/theme/theme.ts");
await initTheme(false);
const { SessionSelectorComponent } = await load("../src/active-port/coding-agent/modes/components/session-selector.ts");
import { assert } from "./helpers.js";

const selected: string[] = [];
const cancelled: string[] = [];

const selector = new SessionSelectorComponent(
	[
		session("sess-1", "Alpha", "/repo/a"),
		session("sess-2", "Second session", "/repo/b"),
	],
	path => selected.push(path),
	() => cancelled.push("cancel"),
	() => cancelled.push("exit"),
);

const frame = selector.render(100).join("\n");
assert(frame.includes("Resume Session"), "OMP session selector should render the upstream selector heading");
assert(frame.includes("Second session"), "OMP session selector should render ACP-backed session rows");
assert(frame.includes("Enter to select"), "OMP session selector should render upstream keybinding hints");

selector.handleInput("\x1b[B");
selector.handleInput("\r");
assert(selected[0] === "sess-2", "OMP session selector should select the highlighted session path");

console.log("PASS: OMP session selector component");

function session(id: string, title: string, cwd: string) {
	return {
		path: id,
		id,
		cwd,
		title,
		created: new Date("2026-04-28T00:00:00Z"),
		modified: new Date("2026-04-28T01:00:00Z"),
		messageCount: 0,
		firstMessage: title,
		allMessagesText: `${title} ${cwd} ${id}`,
	};
}
