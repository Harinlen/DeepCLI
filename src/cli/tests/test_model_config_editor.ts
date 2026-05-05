import { assert } from "./helpers.js";

const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { initTheme } = await load("../src/active-port/coding-agent/modes/theme/theme.ts");
await initTheme(false);
const { ModelConfigEditorComponent } = await load("../src/active-port/coding-agent/modes/components/model-config-editor.ts");
const { CURSOR_MARKER } = await load("../src/active-port/tui/tui.ts");

type ModelConfigUpdate = {
	providerName: string;
	providerType: string;
	modelId: string;
};

const updates: ModelConfigUpdate[] = [];
const tui = { requestRender: () => {} } as any;
const model = {
	displayName: "DeepSeek Chat",
	providerName: "deepseek",
	providerType: "deepseek",
	providerBaseUrl: null,
	providerEffectiveBaseUrl: "https://api.deepseek.com",
	providerAwsRegion: null,
	providerHasApiKey: true,
	providerApiKeyDisplay: "sk-...",
	providerHasAwsSecretKey: false,
	providerAwsSecretKeyDisplay: null,
	providerSettingFields: ["api_key", "base_url"],
	modelId: "deepseek-chat",
	roles: ["default"],
	contextWindow: 128_000,
};

const editor = new ModelConfigEditorComponent(
	tui,
	model,
	1,
	[
		{ type: "deepseek", settingFields: ["api_key", "base_url"], effectiveBaseUrl: "https://api.deepseek.com" },
		{ type: "nvidia", settingFields: ["api_key", "base_url"], effectiveBaseUrl: "https://integrate.api.nvidia.com/v1" },
		{ type: "bedrock", settingFields: ["api_key", "aws_secret_key", "aws_region"] },
	],
	{ providerEditable: true },
	(update: ModelConfigUpdate) => {
		updates.push(update);
	},
	() => {},
);

editor.focused = true;
assert(editor.render(100).join("\n").includes(CURSOR_MARKER), "focused editor should render a cursor immediately");
editor.handleInput("\x1b[B");
const beforeTyping = Bun.stripANSI(editor.render(100).join("\n"));
editor.handleInput("x");
const afterTyping = Bun.stripANSI(editor.render(100).join("\n"));
assert(afterTyping.includes("Name:           deepseek"), "provider name should use the name label");
assert(afterTyping.includes("Type:           < deepseek >"), "provider type should render as an inline selector");
assert(afterTyping === beforeTyping, "provider type should ignore printable text input");

editor.handleInput(" ");
const afterSelect = Bun.stripANSI(editor.render(100).join("\n"));
assert(afterSelect.includes("Type:           < nvidia >"), "provider type should change through selection controls");
assert(afterSelect.includes("Base URL:       https://integrate.api.nvidia.com/v1"), "provider type selection should update effective defaults");
assert(!afterSelect.includes("sk-..."), "provider type selection should not carry old provider credentials into the new type");

editor.handleInput("\n");
assert(updates[0]?.providerType === "nvidia", "save should use the selected provider type");

console.log("PASS: model config editor");
