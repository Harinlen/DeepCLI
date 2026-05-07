import { MustangAgentSessionAdapter } from "../src/session/agent-session-adapter.js";
import { assert } from "./helpers.js";

const adapter = new MustangAgentSessionAdapter(
	{
		client: {} as never,
		sessionService: {
			clientForSession: () => ({}),
			create: async () => ({ sessionId: "new-session" }),
		} as never,
	},
	{
		listProfiles: async () => ({ profiles: [], defaultModel: "" }),
		listProviders: async () => ({
			providers: [],
			providerTypeOptions: [],
			models: [],
			currentUsed: {},
			defaultContextWindow: 128_000,
		}),
	} as never,
);

await adapter.refreshModelProfiles();

assert(adapter.configWarnings.length === 1, "adapter should warn when no models are configured");
assert(adapter.configWarnings[0] === "No models available. Use /model add to add a model.", "no-model warning should point to /model add");

console.log("PASS: no-model warning copy");
