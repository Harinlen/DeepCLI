import { MustangMethod } from "../src/acp/methods.js";
import { WebFetchService } from "../src/webfetch/service.js";
import { assert } from "./helpers.js";

const calls: Array<{ method: string; params: unknown; timeoutMs?: number }> = [];
const service = new WebFetchService({
	async request<R = unknown>(method: string, params: unknown, opts?: { timeoutMs?: number }): Promise<R> {
		calls.push({ method, params, timeoutMs: opts?.timeoutMs });
		if (method === MustangMethod.webFetchBackendOptions) {
			return {
				current: "auto",
				options: [
					{
						id: "httpx",
						label: "HTTPX",
						category: "builtin-local",
						cost: "free",
						role: "Direct fetch",
						installed: true,
						hasCredentials: true,
						available: true,
						setupRequired: false,
						current: false,
					},
				],
			} as R;
		}
		if (method === MustangMethod.webFetchSetBackend) {
			return { backend: "httpx", changed: true, setupRequired: false } as R;
		}
		if (method === MustangMethod.webFetchGetConfig) {
			return { backend: "httpx", backends: { tavily: { api_key: "configured" } } } as R;
		}
		if (method === MustangMethod.webFetchSetConfig) {
			return { backend: "httpx", backends: { crawl4ai: { timeout_seconds: 45 } } } as R;
		}
		throw new Error(`unexpected method ${method}`);
	},
});

const state = await service.backendOptions();
assert(state.current === "auto", "backendOptions should preserve current backend");
assert(state.options[0]?.id === "httpx", "backendOptions should map option ids");

await service.setBackend("httpx", false, "secret-key");
const setBackendCall = calls.find(call => call.method === MustangMethod.webFetchSetBackend);
assert(
	JSON.stringify(setBackendCall?.params) === JSON.stringify({ backend: "httpx", runSetup: false, apiKey: "secret-key" }),
	"setBackend should send backend, runSetup, and optional apiKey",
);
assert(setBackendCall?.timeoutMs === 120_000, "setBackend should allow network-backed validation to finish");

const config = await service.getConfig();
assert(config.backend === "httpx", "getConfig should map backend");
assert(config.backends.tavily?.api_key === "configured", "getConfig should expose public credential status");

const updated = await service.setConfig("crawl4ai.timeout_seconds", 45);
assert(updated.backends.crawl4ai?.timeout_seconds === 45, "setConfig should return updated backend config");
assert(
	calls.find(call => call.method === MustangMethod.webFetchSetConfig)?.timeoutMs === 120_000,
	"setConfig should allow network-backed API key validation to finish",
);
