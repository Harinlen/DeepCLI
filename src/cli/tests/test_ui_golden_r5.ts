import { assert } from "./helpers.js";

const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { initTheme, getEditorTheme } = await load("../src/active-port/coding-agent/modes/theme/theme.ts");
await initTheme(false);

const { WelcomeComponent } = await load("../src/active-port/coding-agent/modes/components/welcome.ts");
const { StatusLineComponent } = await load("../src/active-port/coding-agent/modes/components/status-line.ts");
const { CustomEditor } = await load("../src/active-port/coding-agent/modes/components/custom-editor.ts");
const { SelectList } = await load("../src/active-port/tui/components/select-list.ts");
const { UserMessageComponent } = await load("../src/active-port/coding-agent/modes/components/user-message.ts");
const { AssistantMessageComponent } = await load("../src/active-port/coding-agent/modes/components/assistant-message.ts");
const { BashExecutionComponent } = await load("../src/active-port/coding-agent/modes/components/bash-execution.ts");
const { ToolExecutionComponent } = await load("../src/active-port/coding-agent/modes/components/tool-execution.ts");
const { HookSelectorComponent } = await load("../src/active-port/coding-agent/modes/components/hook-selector.ts");
const { visibleWidth } = await load("../src/active-port/tui/utils.ts");

type GoldenFrame = {
	name: string;
	lines: string[];
	mustInclude: string[];
};

const fakeSession = {
	model: { id: "no-model", name: "no-model", provider: "ACP" },
	thinkingLevel: "off",
	agent: { state: { messages: [] } },
	sessionManager: {
		getCwd: () => process.cwd(),
		getSessionName: () => undefined,
		getUsageStatistics: () => ({ premiumRequests: 0 }),
		titleSource: undefined,
	},
	state: { messages: [], model: { id: "no-model", name: "no-model", provider: "ACP" } },
	isFastModeEnabled: () => false,
	getAsyncJobSnapshot: () => ({ running: [] }),
	modelRegistry: { isUsingOAuth: () => false },
};

const ui = { requestRender() {}, terminal: { columns: 80, rows: 24 } };
const statusLine = new StatusLineComponent(fakeSession as never);
const shortEditor = new CustomEditor(getEditorTheme());
shortEditor.setText("hello");
shortEditor.setTopBorder(statusLine.getTopBorder(70));
const multilineEditor = new CustomEditor(getEditorTheme());
multilineEditor.setText("first line\nsecond line");
multilineEditor.setTopBorder(statusLine.getTopBorder(70));

const selectList = new SelectList(
	[
		{ value: "info", label: "info", description: "Show session info and stats" },
		{ value: "delete", label: "delete", description: "Delete current session and return to selector" },
	],
	8,
	getEditorTheme().selectList,
	{ minPrimaryColumnWidth: 12, maxPrimaryColumnWidth: 32 },
);
const selectInitial = selectList.render(80);
selectList.handleInput("\x1b[B");
const selectDown = selectList.render(80);

const assistant = new AssistantMessageComponent({
	role: "assistant",
	content: [
		{ type: "thinking", thinking: "checking state" },
		{ type: "text", text: "Hello **world**\n\n```ts\nconst ok = true\n```" },
	],
	stopReason: "stop",
	timestamp: 0,
} as never, false);
const userMessage = new UserMessageComponent("不是这么做的吧，应该就是打个 tag");

const bashRunning = new BashExecutionComponent("echo ok", ui as never);
bashRunning.appendOutput("ok\n");
const bashRunningLines = bashRunning.render(80);
bashRunning.setComplete(0, false, { output: "ok\n" });
const bashComplete = new BashExecutionComponent("echo ok", ui as never);
bashComplete.appendOutput("ok\n");
bashComplete.setComplete(0, false, { output: "ok\n" });

const toolPending = new ToolExecutionComponent("grep", { pattern: "foo", path: "src" }, {}, undefined, ui as never, process.cwd(), "t1");
const toolComplete = new ToolExecutionComponent("grep", { pattern: "foo", path: "src" }, {}, undefined, ui as never, process.cwd(), "t2");
toolComplete.updateResult({ content: [{ type: "text", text: "done" }] }, false, "t2");
const toolFailed = new ToolExecutionComponent("grep", { pattern: "foo" }, {}, undefined, ui as never, process.cwd(), "t3");
toolFailed.updateResult({ content: [{ type: "text", text: "boom" }], isError: true }, false, "t3");

const hookSelector = new HookSelectorComponent("Allow command?", ["Allow once", "Deny"], () => {}, () => {}, { maxVisible: 5 });

const warningLine = "Warning: No models available. Use /login or set an API key environment variable, then use /model to select a model.";
const warningWithAutocomplete = [
	"Warning: No models available. Use /login or set an API key environment variable,",
	"then use /model to select a model.",
	statusLine.getTopBorder(80).content,
	...selectInitial,
];

const frames: GoldenFrame[] = [
	{
		name: "welcome first screen",
		lines: new WelcomeComponent("0.1.0", "no-model", "ACP", [], []).render(90),
		mustInclude: ["DeepCLI v0.1.0", "Welcome back!", "⣿", "Tips", "No LSP servers", "No recent sessions"],
	},
	{
		name: "welcome with long model keeps right column",
		lines: new WelcomeComponent("0.1.0", "bedrock/us.anthropic.claude-sonnet-4-6", "bedrock", [], []).render(90),
		mustInclude: ["Welcome back!", "Tips", "No LSP servers", "Recent sessions"],
	},
	{
		name: "empty status line",
		lines: [statusLine.getTopBorder(80).content],
		mustInclude: ["⏺", "no-model", "0.0%"],
	},
	{
		name: "short editor with status border",
		lines: shortEditor.render(76),
		mustInclude: ["no-model", "hello"],
	},
	{
		name: "multiline editor with status border",
		lines: multilineEditor.render(76),
		mustInclude: ["first line", "second line"],
	},
	{
		name: "session autocomplete initial",
		lines: selectInitial,
		mustInclude: ["info", "Show session info and stats", "delete", "Delete current session"],
	},
	{
		name: "session autocomplete selected down",
		lines: selectDown,
		mustInclude: ["❯ delete", "Show session info and stats"],
	},
	{
		name: "no-model warning plus autocomplete",
		lines: warningWithAutocomplete,
		mustInclude: ["Warning: No models available", "no-model", "info", "delete"],
	},
	{
		name: "assistant markdown and thinking",
		lines: assistant.render(80),
		mustInclude: ["checking state", "Hello world", "const ok = true"],
	},
	{
		name: "user message highlighted block",
		lines: userMessage.render(80),
		mustInclude: ["> 不是这么做的吧，应该就是打个 tag"],
	},
	{
		name: "bash running",
		lines: bashRunningLines,
		mustInclude: ["$ echo ok", "Running"],
	},
	{
		name: "bash completed",
		lines: bashComplete.render(80),
		mustInclude: ["$ echo ok", "ok"],
	},
	{
		name: "tool pending",
		lines: toolPending.render(80),
		mustInclude: ["pending grep", 'pattern="foo"', 'path="src"'],
	},
	{
		name: "tool completed",
		lines: toolComplete.render(80),
		mustInclude: ["success grep", "done"],
	},
	{
		name: "tool failed",
		lines: toolFailed.render(80),
		mustInclude: ["error grep", "boom"],
	},
	{
		name: "permission selector overlay",
		lines: hookSelector.render(80),
		mustInclude: ["Allow command?", "❯ Allow once", "Deny", "esc cancel"],
	},
];

for (const frame of frames) {
	const rendered = normalize(frame.lines);
	assert(rendered.trim().length > 0, `${frame.name} should render non-empty output`);
	for (const expected of frame.mustInclude) {
		assert(rendered.includes(expected), `${frame.name} golden frame should include ${JSON.stringify(expected)}\n${rendered}`);
	}
	assertNoOverflow(frame.name, frame.lines, 90);
}

const cjkWelcomeWidth = 70;
const cjkWelcome = new WelcomeComponent(
	"0.1.0",
	"deepseek/deepseek-v4-pro",
	"deepseek",
	[
		{ name: "帮我看一下这个题怎么做：https://acm.hdu.edu.cn/showproblem.php?pid=1055", timeAgo: "11m ago" },
		{ name: "那这个呢：https://acm.hdu.edu.cn/showproblem.php?pid=1087", timeAgo: "16m ago" },
	],
	[],
).render(cjkWelcomeWidth + 2);
assertNoOverflow("welcome with CJK recent sessions", cjkWelcome, cjkWelcomeWidth);

console.log(`PASS: R5 UI golden frames (${frames.length + 1})`);
process.exit(0);

function normalize(lines: string[]): string {
	return lines.map(line => Bun.stripANSI(line).trimEnd()).join("\n");
}

function assertNoOverflow(name: string, lines: string[], maxWidth: number): void {
	for (const line of normalize(lines).split("\n")) {
		assert(visibleWidth(line) <= maxWidth, `${name} golden frame line should fit width ${maxWidth}: ${line}`);
	}
}
