import { ModelService } from "../src/models/service.js";
import { assert } from "./helpers.js";

const calls: Array<{ method: string; params: unknown }> = [];
const service = new ModelService({
	async request<R = unknown>(method: string, params: unknown): Promise<R> {
		calls.push({ method, params });
		if (method === "_mustang.agent/model/provider_list") {
			return {
				defaultContextWindow: 128_000,
				currentUsed: {
					default: ["deepseek", "deepseek-chat"],
					compact: ["local", "qwen3"],
					memory: ["nvidia", "minimaxai/minimax-m2.7"],
				},
				providers: [
					{
						name: "deepseek",
						providerType: "deepseek",
						models: ["deepseek-chat", "deepseek-reasoner"],
						contextWindows: { "deepseek-chat": 1_000_000, "deepseek-reasoner": 128_000 },
						roles: { default: true, compact: false },
					},
					{
						name: "local",
						providerType: "openai_compatible",
						models: ["qwen3"],
						contextWindows: { qwen3: 128_000 },
						roles: { default: false, compact: true },
					},
					{
						name: "nvidia",
						providerType: "nvidia",
						models: ["minimaxai/minimax-m2.7"],
						contextWindows: {},
						roles: { default: false, compact: false, memory: true },
					},
				],
			} as R;
		}
		if (method === "_mustang.agent/model/set_current") {
			const request = params as { role?: string; provider?: string; model?: string };
			return {
				role: request.role ?? "default",
				model: [request.provider, request.model],
			} as R;
		}
		assert(method === "_mustang.agent/model/profile_list", "model service should call a known model method");
		return {
			defaultModel: "deepseek/deepseek-chat",
			profiles: [
				{
					name: "deepseek/deepseek-chat",
					providerName: "deepseek",
					providerType: "deepseek",
					modelId: "deepseek-chat",
					contextWindow: 64_000,
					isDefault: true,
				},
				{
					name: "local/qwen3",
					providerName: "local",
					providerType: "openai_compatible",
					modelId: "qwen3",
					contextWindow: 128_000,
					isDefault: false,
				},
			],
		} as R;
	},
});

const state = await service.listProfiles();
assert(state.profiles[0]?.contextWindow === 64_000, "model service should preserve context window");
assert(state.profiles[0]?.providerName === "deepseek", "model service should derive provider name");
assert(state.defaultModel === "deepseek/deepseek-chat", "model service should preserve default model");

const providerState = await service.listProviders();
assert(providerState.defaultContextWindow === 128_000, "provider list should expose kernel default context window");
assert(providerState.models.length === 4, "provider list should flatten provider models");
assert(providerState.models[0]?.displayName === "deepseek-chat", "provider list should expose model display name");
assert(providerState.models[0]?.roles.includes("default"), "provider list should mark default role");
assert(providerState.models[2]?.roles.includes("compact"), "provider list should mark compact role");
assert(providerState.models[0]?.contextWindow === 1_000_000, "provider list should prefer kernel provider context window");
assert(providerState.models[2]?.contextWindow === 128_000, "provider list should merge context window by provider/model");
assert(providerState.models[3]?.contextWindow === 128_000, "provider list should fall back to kernel default context window");
assert(providerState.currentUsed.compact?.[0] === "local", "provider list should preserve current_used roles");

const setResult = await service.setCurrent("compact", "local", "qwen3");
assert(setResult.role === "compact", "setCurrent should preserve role");
assert(setResult.provider === "local" && setResult.model === "qwen3", "setCurrent should preserve model ref");
const setCall = calls.find(call => call.method === "_mustang.agent/model/set_current");
assert(JSON.stringify(setCall?.params) === JSON.stringify({ role: "compact", provider: "local", model: "qwen3" }), "setCurrent should send role/provider/model");

console.log("PASS: model service");
