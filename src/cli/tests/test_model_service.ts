import { ModelService } from "../src/models/service.js";
import { assert } from "./helpers.js";

const calls: Array<{ method: string; params: unknown }> = [];
const service = new ModelService({
	async request<R = unknown>(method: string, params: unknown): Promise<R> {
		calls.push({ method, params });
		if (method === "_mustang.agent/model/provider_list") {
			return {
				defaultContextWindow: 128_000,
				providerTypeOptions: [
					{ providerType: "deepseek", settingFields: ["api_key", "base_url"], effectiveBaseUrl: "https://api.deepseek.com" },
					{ providerType: "nvidia", settingFields: ["api_key", "base_url"], effectiveBaseUrl: "https://integrate.api.nvidia.com/v1" },
				],
				currentUsed: {
					default: ["deepseek", "deepseek-chat"],
					compact: ["local", "qwen3"],
					memory: ["nvidia", "minimaxai/minimax-m2.7"],
				},
				providers: [
					{
						name: "deepseek",
						providerType: "deepseek",
						hasApiKey: true,
						apiKeyDisplay: "sk-deepseek-chat",
						effectiveBaseUrl: "https://api.deepseek.com",
						settingFields: ["api_key", "base_url"],
						models: ["deepseek-chat", "deepseek-reasoner"],
						contextWindows: { "deepseek-chat": 1_000_000, "deepseek-reasoner": 128_000 },
						displayNames: { "deepseek-chat": "DeepSeek Chat" },
						roles: { default: true, compact: false },
					},
					{
						name: "local",
						providerType: "openai_compatible",
						baseUrl: "http://localhost:11434/v1",
						settingFields: ["api_key", "base_url"],
						models: ["qwen3"],
						contextWindows: { qwen3: 128_000 },
						roles: { default: false, compact: true },
					},
					{
						name: "nvidia",
						providerType: "nvidia",
						settingFields: ["api_key", "base_url"],
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
		if (method === "_mustang.agent/model/update") {
			const request = params as { provider?: string; model?: string; providerName?: string; providerType?: string; baseUrl?: string; modelId?: string; displayName?: string; contextWindow?: number; roles?: string[] };
			return {
				model: [request.providerName ?? request.provider, request.modelId ?? request.model],
				providerType: request.providerType ?? "deepseek",
				baseUrl: request.baseUrl ?? null,
				effectiveBaseUrl: request.baseUrl ?? "https://api.deepseek.com",
				awsRegion: null,
				hasApiKey: true,
				apiKeyDisplay: "sk-deepseek-chat",
				hasAwsSecretKey: false,
				settingFields: ["api_key", "base_url"],
				displayName: request.displayName,
				contextWindow: request.contextWindow,
				roles: request.roles ?? [],
			} as R;
		}
		if (method === "_mustang.agent/model/add") {
			const request = params as { providerName?: string; providerType?: string; modelId?: string; displayName?: string; contextWindow?: number; roles?: string[] };
			return {
				model: [request.providerName, request.modelId],
				providerType: request.providerType ?? "deepseek",
				baseUrl: null,
				effectiveBaseUrl: "https://api.deepseek.com",
				awsRegion: null,
				hasApiKey: false,
				hasAwsSecretKey: false,
				settingFields: ["api_key", "base_url"],
				displayName: request.displayName,
				contextWindow: request.contextWindow,
				roles: request.roles ?? [],
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
assert(providerState.models[0]?.displayName === "DeepSeek Chat", "provider list should expose model display name");
assert(providerState.models[0]?.providerHasApiKey === true, "provider list should expose api-key presence");
assert(providerState.models[0]?.providerApiKeyDisplay === "sk-deepseek-chat", "provider list should expose api-key display");
assert(providerState.models[0]?.providerEffectiveBaseUrl === "https://api.deepseek.com", "provider list should expose effective base url");
assert(providerState.models[0]?.providerSettingFields.join(",") === "api_key,base_url", "provider list should expose provider setting fields");
assert(providerState.models[2]?.providerBaseUrl === "http://localhost:11434/v1", "provider list should expose provider base url");
assert(providerState.models[0]?.roles.includes("default"), "provider list should mark default role");
assert(providerState.models[2]?.roles.includes("compact"), "provider list should mark compact role");
assert(providerState.models[0]?.contextWindow === 1_000_000, "provider list should prefer kernel provider context window");
assert(providerState.models[2]?.contextWindow === 128_000, "provider list should merge context window by provider/model");
assert(providerState.models[3]?.contextWindow === 128_000, "provider list should fall back to kernel default context window");
assert(providerState.currentUsed.compact?.[0] === "local", "provider list should preserve current_used roles");
assert(providerState.providerTypeOptions.some(option => option.providerType === "nvidia"), "provider list should expose provider type options");

const setResult = await service.setCurrent("compact", "local", "qwen3");
assert(setResult.role === "compact", "setCurrent should preserve role");
assert(setResult.provider === "local" && setResult.model === "qwen3", "setCurrent should preserve model ref");
const setCall = calls.find(call => call.method === "_mustang.agent/model/set_current");
assert(JSON.stringify(setCall?.params) === JSON.stringify({ role: "compact", provider: "local", model: "qwen3" }), "setCurrent should send role/provider/model");

const updateResult = await service.updateModel({
	providerName: "deepseek",
	newProviderName: "deepseek-prod",
	providerType: "deepseek",
	baseUrl: "https://api.deepseek.example",
	modelId: "deepseek-chat",
	newModelId: "deepseek-chat-v2",
	displayName: "Chat",
	contextWindow: 200_000,
	roles: ["default"],
});
assert(updateResult.displayName === "Chat", "updateModel should preserve display name");
assert(updateResult.providerName === "deepseek-prod", "updateModel should preserve provider name");
assert(updateResult.modelId === "deepseek-chat-v2", "updateModel should preserve model id");
assert(updateResult.contextWindow === 200_000, "updateModel should preserve context window");
const updateCall = calls.find(call => call.method === "_mustang.agent/model/update");
assert(
	JSON.stringify(updateCall?.params) === JSON.stringify({
		provider: "deepseek",
		model: "deepseek-chat",
		providerName: "deepseek-prod",
		providerType: "deepseek",
		baseUrl: "https://api.deepseek.example",
		modelId: "deepseek-chat-v2",
		displayName: "Chat",
		contextWindow: 200_000,
		roles: ["default"],
	}),
	"updateModel should send provider/model settings",
);

const addResult = await service.addModel({
	providerName: "nvidia",
	providerType: "nvidia",
	modelId: "new-model",
	displayName: "New Model",
	contextWindow: 64_000,
	roles: ["compact"],
});
assert(addResult.providerName === "nvidia" && addResult.modelId === "new-model", "addModel should preserve provider/model ref");
const addCall = calls.find(call => call.method === "_mustang.agent/model/add");
assert(
	JSON.stringify(addCall?.params) === JSON.stringify({
		providerName: "nvidia",
		providerType: "nvidia",
		modelId: "new-model",
		displayName: "New Model",
		contextWindow: 64_000,
		roles: ["compact"],
	}),
	"addModel should send add params",
);

console.log("PASS: model service");
