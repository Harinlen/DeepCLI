import { assert } from "./helpers.js";

const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { initTheme } = await load("../src/active-port/coding-agent/modes/theme/theme.ts");
await initTheme(false);
const { WebFetchConfigEditorComponent } = await load("../src/active-port/coding-agent/modes/components/webfetch-config-editor.ts");
const { CURSOR_MARKER } = await load("../src/active-port/tui/tui.ts");

const updates: Array<Array<{ path: string; value: unknown; kind: string }>> = [];
let cancelled = false;
const tui = { requestRender: () => {} } as any;

const editor = new WebFetchConfigEditorComponent(
	tui,
	"WebFetch config (tavily)",
	[
		{ label: "API key:", path: "tavily.api_key", kind: "secret", status: "configured" },
		{ label: "timeout:", path: "tavily.timeout_seconds", kind: "value", value: 30 },
	],
	(items: Array<{ path: string; value: unknown; kind: string }>) => {
		updates.push(items);
	},
	() => {
		cancelled = true;
	},
);

editor.focused = true;
let rawRendered = editor.render(100).join("\n");
let rendered = normalize(rawRendered);
assert(rendered.includes("WebFetch config (tavily)"), "editor should render a form title");
assert(rendered.includes("API key:          <configured>"), "editor should show credential status inline");
assert(rendered.includes("timeout:          30"), "editor should show regular config values inline");
assert(rawRendered.includes(CURSOR_MARKER), "focused editor should render a cursor");

editor.handleInput("\x1b[B");
editor.handleInput("\x7f");
editor.handleInput("\x7f");
editor.handleInput("45");
rawRendered = editor.render(100).join("\n");
rendered = normalize(rawRendered);
assert(rendered.includes("timeout:          45"), "regular fields should be editable in-place");

editor.handleInput("\n");
assert(updates[0]?.length === 1, "saving should only submit changed fields");
assert(updates[0]?.[0]?.path === "tavily.timeout_seconds", "saved field should preserve path");
assert(updates[0]?.[0]?.value === 45, "numeric field should parse before save");
assert(cancelled === false, "save should not cancel when there are changes");

const secretEditor = new WebFetchConfigEditorComponent(
	tui,
	"WebFetch config (tavily)",
	[{ label: "API key:", path: "tavily.api_key", kind: "secret", status: "configured" }],
	(items: Array<{ path: string; value: unknown; kind: string }>) => {
		updates.push(items);
	},
	() => {
		cancelled = true;
	},
);
secretEditor.focused = true;
secretEditor.handleInput("tvly-new");
secretEditor.handleInput("\n");
assert(updates[1]?.[0]?.path === "tavily.api_key", "secret field should save through its config path");
assert(updates[1]?.[0]?.value === "tvly-new", "secret field should save typed value");

console.log("PASS: webfetch config editor");

function normalize(text: string): string {
	return Bun.stripANSI(text.replaceAll(CURSOR_MARKER, ""));
}
