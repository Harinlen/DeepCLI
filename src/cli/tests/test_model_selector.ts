import { assert } from "./helpers.js";

const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { initTheme } = await load("../src/active-port/coding-agent/modes/theme/theme.ts");
await initTheme(false);
const { ModelSelectorComponent } = await load("../src/active-port/coding-agent/modes/components/model-selector.ts");

const selector = new ModelSelectorComponent(
	{ requestRender: () => {} } as any,
	async () => ({
		providers: [],
		providerTypeOptions: [],
		models: [],
		currentUsed: {},
		defaultContextWindow: 128_000,
	}),
	() => {},
	() => {},
);

await new Promise(resolve => setTimeout(resolve, 0));

const rendered = Bun.stripANSI(selector.render(100).join("\n"));
assert(rendered.includes("No models available. Use /model add to add a model."), "empty model selector should point to /model add");

console.log("PASS: model selector empty state");
