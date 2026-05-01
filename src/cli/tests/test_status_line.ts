import { assert } from "./helpers.js";

const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { initTheme } = await load("../src/active-port/coding-agent/modes/theme/theme.ts");
const { StatusLineComponent } = await load("../src/active-port/coding-agent/modes/components/status-line.ts");

await initTheme(false);

const statusLine = new StatusLineComponent({
	model: { id: "claude-sonnet", name: "sonnet", provider: "anthropic" },
	thinkingLevel: "off",
	agent: {
		state: {
			messages: [
				{
					role: "assistant",
					stopReason: "stop",
					usage: { input: 1200, output: 300 },
				},
			],
		},
	},
	sessionManager: {
		getCwd: () => "/tmp",
		getSessionName: () => "Test",
		getUsageStatistics: () => ({ premiumRequests: 0 }),
		titleSource: "user",
	},
	state: {
		messages: [
			{
				role: "assistant",
				stopReason: "stop",
				usage: { input: 1200, output: 300 },
			},
		],
		model: { id: "claude-sonnet", name: "sonnet", provider: "anthropic" },
	},
	isFastModeEnabled: () => false,
	getAsyncJobSnapshot: () => ({ running: [] }),
	modelRegistry: { isUsingOAuth: () => false },
} as never);

const border = statusLine.getTopBorder(80);
assert(border.width > 0, "status line top border should render visible content");
assert(border.content.includes("sonnet"), "status line should include model segment");
assert(border.content.includes("mustang") || border.content.includes("/tmp"), "status line should include cwd path segment");
assert(border.content.includes("0.0%") || border.content.includes("◫"), "status line should include context segment");

const noModelStatusLine = new StatusLineComponent({
	model: { id: "no-model", name: "no-model", provider: "ACP" },
	agent: { state: { messages: [] } },
	sessionManager: {
		getCwd: () => "/tmp",
		getSessionName: () => undefined,
		getUsageStatistics: () => ({ premiumRequests: 0 }),
		titleSource: undefined,
	},
	state: { messages: [], model: { id: "no-model", name: "no-model", provider: "ACP" } },
	isFastModeEnabled: () => false,
	getAsyncJobSnapshot: () => ({ running: [] }),
	modelRegistry: { isUsingOAuth: () => false },
} as never);
assert(noModelStatusLine.getTopBorder(80).content.includes("no-model"), "status line should expose no-model state");

statusLine.setHookStatus("test", "hook ok");
assert(statusLine.render(40)[0]?.includes("hook ok"), "status line render should expose hook status rows");

console.log("PASS: status line");
