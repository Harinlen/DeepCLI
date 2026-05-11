import { assert } from "./helpers.js";

const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { initTheme } = await load("../src/active-port/coding-agent/modes/theme/theme.ts");
await initTheme(false);
const { SelectorController } = await load("../src/active-port/coding-agent/modes/controllers/selector-controller.ts");

let mounted: any;
const calls: string[] = [];
const ctx = {
	session: {
		listWebFetchBackends: async () => ({
			current: "tavily",
			options: [
				{
					id: "tavily",
					label: "Tavily",
					role: "Provider extraction",
					current: true,
					available: false,
					setupRequired: false,
					credentialRequired: true,
				},
			],
		}),
		setWebFetchBackend: async () => {
			calls.push("setWebFetchBackend");
			return { backend: "tavily", changed: false };
		},
	},
	editor: { invalidate() {} },
	editorContainer: {
		clear: () => {
			mounted = undefined;
		},
		addChild: (component: any) => {
			mounted = component;
		},
	},
	ui: {
		setFocus: () => {},
		requestRender: () => calls.push("render"),
	},
	showStatus: (message: string) => calls.push(`status:${message}`),
	showWarning: (message: string) => calls.push(`warning:${message}`),
	showError: (message: string) => calls.push(`error:${message}`),
	showHookInput: async () => {
		calls.push("input");
		return "should-not-be-used";
	},
};

new SelectorController(ctx).showWebFetchBackendSelector();
await Bun.sleep(0);
assert(mounted, "backend selector should mount a component");

mounted.handleInput("\n");

assert(calls.includes("status:WebFetch backend tavily is already selected."), "current backend should be a no-op");
assert(!calls.includes("setWebFetchBackend"), "current backend selection must not call the backend setter");
assert(!calls.includes("input"), "current backend selection must not prompt for an API key");

mounted = undefined;
calls.length = 0;
const invalidCtx = {
	session: {
		listWebFetchBackends: async () => ({
			current: "auto",
			options: [
				{
					id: "tavily",
					label: "Tavily",
					role: "Provider extraction",
					status: "configured",
					current: false,
					available: false,
					setupRequired: false,
					credentialRequired: false,
					hasCredentials: true,
				},
			],
		}),
		setWebFetchBackend: async () => ({
			backend: "tavily",
			changed: false,
			credentialRequired: true,
			credentialRequest: { prompt: "Enter Tavily API key", envKey: "TAVILY_API_KEY", label: "Tavily API key" },
			message: "Tavily API key validation failed.",
		}),
	},
	editor: { invalidate() {} },
	editorContainer: ctx.editorContainer,
	ui: ctx.ui,
	showStatus: ctx.showStatus,
	showWarning: ctx.showWarning,
	showError: (message: string) => calls.push(`error:${message}`),
	showHookInput: async (title: string, placeholder?: string) => {
		calls.push(`input:${title}:${placeholder}`);
		return "tvly-replacement";
	},
};

new SelectorController(invalidCtx).showWebFetchBackendSelector();
await Bun.sleep(0);
assert(mounted, "configured backend selector should mount a component");
assert(Bun.stripANSI(mounted.render(100).join("\n")).includes("configured - Provider extraction"), "configured API backend should not be shown as available");

mounted.handleInput("\n");
await Bun.sleep(0);

assert(calls.includes("error:Tavily API key validation failed."), "invalid configured key should show validation error");
assert(calls.includes("input:Enter replacement Tavily API key:TAVILY_API_KEY"), "invalid configured key should ask for a replacement key after showing the error");

mounted = undefined;
calls.length = 0;
const setupCtx = {
	session: {
		listWebFetchBackends: async () => ({
			current: "auto",
			options: [
				{
					id: "crawl4ai",
					label: "Crawl4AI",
					role: "Local browser rendering",
					status: "setup_needed",
					current: false,
					available: false,
					setupRequired: true,
					credentialRequired: false,
				},
			],
		}),
		setWebFetchBackend: async (_backend: string, runSetup: boolean) => {
			calls.push(`set:${runSetup}`);
			if (!runSetup) {
				return {
					backend: "crawl4ai",
					changed: false,
					setupRequired: true,
					setupPlan: { commands: ["uv pip install --python /deepcli/python crawl4ai"] },
				};
			}
			return { backend: "crawl4ai", changed: true, setupRequired: false, message: "WebFetch backend set to crawl4ai." };
		},
	},
	editor: { invalidate() {} },
	editorContainer: ctx.editorContainer,
	ui: ctx.ui,
	showStatus: ctx.showStatus,
	showWarning: ctx.showWarning,
	showError: ctx.showError,
	showHookConfirm: async (title: string) => {
		calls.push(`confirm:${title}`);
		return true;
	},
	setWorkingMessage: (message?: string) => calls.push(`working:${message ?? ""}`),
	ensureLoadingAnimation: () => calls.push("loader"),
	loadingAnimation: { stop: () => calls.push("loader-stop") },
	statusContainer: { clear: () => calls.push("status-clear") },
};

new SelectorController(setupCtx).showWebFetchBackendSelector();
await Bun.sleep(0);
assert(mounted, "setup backend selector should mount a component");
mounted.handleInput("\n");
await Bun.sleep(0);

assert(calls.includes("confirm:Install crawl4ai"), "setup backend selector should ask before installing");
assert(calls.includes("status:Installing WebFetch backend crawl4ai..."), "setup backend selector should show install status");
assert(calls.includes("loader"), "setup backend selector should mount a loader while installing");
assert(calls.includes("set:true"), "setup backend selector should retry with runSetup");
assert(calls.includes("loader-stop"), "setup backend selector should stop the loader");
assert(calls.includes("status:WebFetch backend set to crawl4ai."), "setup backend selector should report success after install");

console.log("PASS: webfetch backend selector");
