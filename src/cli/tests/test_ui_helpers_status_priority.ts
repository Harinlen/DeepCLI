import { assert } from "./helpers.js";

const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { UiHelpers } = await load("../src/active-port/coding-agent/modes/utils/ui-helpers.ts");

function makeStatusContainer() {
	const calls: string[] = [];
	const container = {
		children: [] as any[],
		addChild(child: any) {
			this.children.push(child);
			calls.push("add");
		},
		removeChild(child: any) {
			const index = this.children.indexOf(child);
			if (index !== -1) this.children.splice(index, 1);
			calls.push("remove");
		},
		clear() {
			this.children = [];
			calls.push("clear");
		},
	};
	return { container, calls };
}

{
	const { container, calls } = makeStatusContainer();
	const loader = { render: () => ["Working"], invalidate: () => {} };
	container.addChild(loader);
	const ctx: any = {
		loadingAnimation: loader,
		statusContainer: container,
		ui: { requestRender: () => calls.push("render") },
	};

	new UiHelpers(ctx).showStatus("Thinking blocks: hidden");

	assert(container.children[0] === loader, "active Working loader should remain the first status child");
	assert(container.children.length === 1, "status text should not be shown while Working is active");
	assert(!calls.includes("clear"), "mounted Working loader should not be cleared by showStatus");
}

{
	const { container } = makeStatusContainer();
	const loader = { render: () => ["Working"], invalidate: () => {} };
	const ctx: any = {
		loadingAnimation: loader,
		statusContainer: container,
		ui: { requestRender: () => {} },
	};

	new UiHelpers(ctx).showStatus("Thinking blocks: visible");

	assert(container.children[0] === loader, "detached Working loader should be reattached");
	assert(container.children.length === 1, "status text should not be shown after reattaching Working");
}

{
	const { container } = makeStatusContainer();
	const ctx: any = {
		loadingAnimation: undefined,
		statusContainer: container,
		ui: { requestRender: () => {} },
	};

	new UiHelpers(ctx).showStatus("Thinking blocks: hidden");

	assert(container.children.length === 1, "status text should be shown when Working is inactive");
	assert(container.children[0].render()[0] === "Thinking blocks: hidden", "inactive status text should render");
}

console.log("PASS: UI helpers status priority");
