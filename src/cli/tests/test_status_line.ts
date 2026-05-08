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
		getContextUsage: () => ({ totalTokens: 1500, contextWindow: 200_000, percent: 0.8 }),
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

const border = statusLine.getTopBorder(100);
assert(border.width > 0, "status line top border should render visible content");
assert(border.content.includes("⏺"), "status line should show connected kernel state");
assert(border.content.includes("Ask"), "status line should show default permission mode as Ask");
assert(border.content.indexOf("Ask") < border.content.indexOf("sonnet"), "permission mode should render before model");
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
		getContextUsage: () => ({ totalTokens: 29_000, contextWindow: 1_000_000, percent: 2.9 }),
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
const millionBorder = millionWindowStatusLine.getTopBorder(100).content;
assert(millionBorder.includes("29K (2.9%/1M)"), `status line should show actual compact tokens plus percent/window, got: ${millionBorder}`);
assert(!millionBorder.includes("1,000,000"), "status line should not render long context windows");

const resumedStatusLine = new StatusLineComponent({
	model: { id: "deepseek", name: "deepseek", provider: "deepseek", contextWindow: 1_000_000 },
	agent: { state: { messages: [] } },
	sessionManager: {
		getCwd: () => "/tmp",
		getSessionName: () => "Resumed",
		getUsageStatistics: () => ({ input: 14_443, output: 125, cacheRead: 0, cacheWrite: 0, cost: 0, premiumRequests: 0 }),
		getContextUsage: () => ({ totalTokens: 0, contextWindow: 1_000_000, percent: 0 }),
		titleSource: "auto",
	},
	state: {
		messages: [],
		model: { id: "deepseek", name: "deepseek", provider: "deepseek", contextWindow: 1_000_000 },
	},
	isFastModeEnabled: () => false,
	getAsyncJobSnapshot: () => ({ running: [] }),
	modelRegistry: { isUsingOAuth: () => false },
} as never);
const resumedBorder = resumedStatusLine.getTopBorder(100).content;
assert(
	resumedBorder.includes("0 (0.0%/1M)"),
	`resumed sessions without a kernel context snapshot should not treat cumulative totals as context, got: ${resumedBorder}`,
);

const noModelStatusLine = new StatusLineComponent({
	model: { id: "no-model", name: "no-model", provider: "ACP" },
	agent: { state: { messages: [] } },
	sessionManager: {
		getCwd: () => "/tmp",
		getSessionName: () => undefined,
		getUsageStatistics: () => ({ premiumRequests: 0 }),
		getContextUsage: () => ({ totalTokens: 0, contextWindow: null, percent: 0 }),
		titleSource: undefined,
	},
	state: { messages: [], model: { id: "no-model", name: "no-model", provider: "ACP" } },
	isFastModeEnabled: () => false,
	getAsyncJobSnapshot: () => ({ running: [] }),
	modelRegistry: { isUsingOAuth: () => false },
} as never);
assert(noModelStatusLine.getTopBorder(80).content.includes("no-model"), "status line should expose no-model state");

const turnStatsStatusLine = new StatusLineComponent({
	model: { id: "claude-sonnet", name: "sonnet", provider: "anthropic", contextWindow: 200_000 },
	agent: { state: { messages: [] } },
	sessionManager: {
		getCwd: () => "/tmp",
		getSessionName: () => undefined,
		getUsageStatistics: () => ({ input: 1200, output: 300, cacheRead: 0, cacheWrite: 0, cost: 0, premiumRequests: 0 }),
		getContextUsage: () => ({ totalTokens: 1500, contextWindow: 200_000, percent: 0.8 }),
		titleSource: undefined,
	},
	state: {
		messages: [
			{
				role: "assistant",
				stopReason: "stop",
				timestamp: Date.now() - 1500,
				duration: 1500,
				usage: { input: 1200, output: 300 },
			},
		],
		model: { id: "claude-sonnet", name: "sonnet", provider: "anthropic", contextWindow: 200_000 },
	},
	isFastModeEnabled: () => false,
	getAsyncJobSnapshot: () => ({ running: [] }),
	modelRegistry: { isUsingOAuth: () => false },
} as never);
turnStatsStatusLine.updateSettings({
	preset: "custom",
	leftSegments: [],
	rightSegments: ["turn_duration"],
	separator: "ascii",
	segmentOptions: {},
});
const turnStatsBorder = turnStatsStatusLine.getTopBorder(80).content;
assert(!turnStatsBorder.includes("1,200"), `status line should not include input tokens, got: ${turnStatsBorder}`);
assert(!turnStatsBorder.includes("300"), `status line should not include output tokens, got: ${turnStatsBorder}`);
assert(turnStatsBorder.includes("2s"), `status line should include latest response duration, got: ${turnStatsBorder}`);

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

const modeCases = [
	["accept_edits", "✎", "Edits"],
	["plan", "▤", "Plan"],
	["auto", "⚡", "Auto"],
	["dont_ask", "⏭", "No ask"],
	["bypass", "⚠", "Bypass"],
];
for (const [mode, icon, label] of modeCases) {
	const content = new StatusLineComponent({
		...connectionSession,
		currentPermissionMode: mode,
	} as never).getTopBorder(80).content;
	assert(content.includes(icon) && content.includes(label), `status line should render ${mode} as ${icon} ${label}, got: ${content}`);
}

console.log("PASS: status line");
