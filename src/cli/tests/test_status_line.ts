import { assert } from "./helpers.js";

const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { initTheme } = await load("../src/active-port/coding-agent/modes/theme/theme.ts");
const { StatusLineComponent } = await load("../src/active-port/coding-agent/modes/components/status-line.ts");

await initTheme(false);

const statusLine = new StatusLineComponent({
	model: { id: "claude-sonnet", name: "sonnet", provider: "anthropic", contextWindow: 200_000 },
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
		model: { id: "claude-sonnet", name: "sonnet", provider: "anthropic", contextWindow: 200_000 },
	},
	kernelConnectionState: "connected",
	isFastModeEnabled: () => false,
	getAsyncJobSnapshot: () => ({ running: [] }),
	modelRegistry: { isUsingOAuth: () => false },
} as never);

const border = statusLine.getTopBorder(80);
assert(border.width > 0, "status line top border should render visible content");
assert(border.content.includes("⏺"), "status line should show connected kernel state");
assert(border.content.includes("sonnet"), "status line should include model segment");
assert(border.content.includes("mustang") || border.content.includes("/tmp"), "status line should include cwd path segment");
assert(border.content.includes("1.5K (0.8%/200K)"), "status line should include computed context usage");

const narrowBorder = statusLine.getTopBorder(34).content;
const expandedBorder = statusLine.getTopBorder(120).content;
assert(narrowBorder.includes("sonnet"), "narrow status line should keep high-priority model segment");
assert(
	expandedBorder.includes("mustang") || expandedBorder.includes("/tmp"),
	"status line should restore width-dependent path segment after a wider render",
);
assert(
	expandedBorder.includes("1.5K (0.8%/200K)"),
	"status line should restore width-dependent context segment after a wider render",
);

const millionWindowStatusLine = new StatusLineComponent({
	model: { id: "deepseek", name: "deepseek", provider: "deepseek", contextWindow: 1_000_000 },
	agent: {
		state: {
			messages: [{ role: "assistant", stopReason: "stop", usage: { input: 29_000, output: 0 } }],
		},
	},
	sessionManager: {
		getCwd: () => "/tmp",
		getSessionName: () => undefined,
		getUsageStatistics: () => ({ premiumRequests: 0 }),
		titleSource: undefined,
	},
	state: {
		messages: [{ role: "assistant", stopReason: "stop", usage: { input: 29_000, output: 0 } }],
		model: { id: "deepseek", name: "deepseek", provider: "deepseek", contextWindow: 1_000_000 },
	},
	isFastModeEnabled: () => false,
	getAsyncJobSnapshot: () => ({ running: [] }),
	modelRegistry: { isUsingOAuth: () => false },
} as never);
const millionBorder = millionWindowStatusLine.getTopBorder(80).content;
assert(millionBorder.includes("29K (2.9%/1M)"), `status line should show actual compact tokens plus percent/window, got: ${millionBorder}`);
assert(!millionBorder.includes("1,000,000"), "status line should not render long context windows");

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

const connectionSession = {
	model: { id: "claude-sonnet", name: "sonnet", provider: "anthropic" },
	agent: { state: { messages: [] } },
	sessionManager: {
		getCwd: () => "/tmp",
		getSessionName: () => undefined,
		getUsageStatistics: () => ({ premiumRequests: 0 }),
		titleSource: undefined,
	},
	state: { messages: [], model: { id: "claude-sonnet", name: "sonnet", provider: "anthropic" } },
	isFastModeEnabled: () => false,
	getAsyncJobSnapshot: () => ({ running: [] }),
	modelRegistry: { isUsingOAuth: () => false },
};
const connectingBorder = new StatusLineComponent({
	...connectionSession,
	kernelConnectionState: "connecting",
} as never).getTopBorder(80).content;
assert(/[◐◓◑◒]/.test(connectingBorder), "status line should show rotating connecting kernel state");
const disconnectedBorder = new StatusLineComponent({
	...connectionSession,
	kernelConnectionState: "disconnected",
} as never).getTopBorder(80).content;
assert(disconnectedBorder.includes("○"), "status line should show disconnected kernel state");

console.log("PASS: status line");
