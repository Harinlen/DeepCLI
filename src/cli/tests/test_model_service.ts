import { ModelService } from "../src/models/service.js";
import { assert } from "./helpers.js";

const service = new ModelService({
	async request<R = unknown>(method: string): Promise<R> {
		assert(method === "_mustang.agent/model/profile_list", "model service should call profile list");
		return {
			defaultModel: "deepseek/deepseek-chat",
			profiles: [
				{
					name: "deepseek/deepseek-chat",
					providerType: "deepseek",
					modelId: "deepseek-chat",
					contextWindow: 64_000,
					isDefault: true,
				},
			],
		} as R;
	},
});

const state = await service.listProfiles();
assert(state.profiles[0]?.contextWindow === 64_000, "model service should preserve context window");
assert(state.defaultModel === "deepseek/deepseek-chat", "model service should preserve default model");

console.log("PASS: model service");
