// @ts-nocheck
import { Container, type Focusable, fuzzyFilter, getKeybindings, Input, Spacer, Text, type TUI } from "@/tui/index.js";
import type { ProviderModelItem, ProviderModelState } from "@/models/service.js";
import { formatCompactNumber } from "@/compat/utils.js";
import { theme } from "../theme/theme";
import { DynamicBorder } from "./dynamic-border";

/**
 * Searchable selector for Kernel-registered LLM models.
 *
 * This mirrors OMP's model selector shape, but the data comes from the
 * Kernel's ``llm.current_used`` model registry instead of local settings.
 */
export class ModelSelectorComponent extends Container implements Focusable {
	#searchInput: Input;
	#listContainer: Container;
	#allModels: ProviderModelItem[] = [];
	#filteredModels: ProviderModelItem[] = [];
	#selectedIndex = 0;
	#errorMessage: string | undefined;
	#focused = false;

	constructor(
		private readonly tui: TUI,
		private readonly loadModels: () => Promise<ProviderModelState>,
		private readonly onSelect: (model: ProviderModelItem) => void | Promise<void>,
		private readonly onCancel: () => void,
		initialSearchInput?: string,
	) {
		super();

		this.addChild(new DynamicBorder());
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("muted", "Current-used roles are shown at the right of each model."), 0, 0));
		this.addChild(new Spacer(1));

		this.#searchInput = new Input();
		this.#searchInput.onSubmit = () => {
			const selected = this.#filteredModels[this.#selectedIndex];
			if (selected) void this.onSelect(selected);
		};
		this.#searchInput.onEscape = () => this.onCancel();
		if (initialSearchInput) this.#searchInput.setValue(initialSearchInput);
		this.addChild(this.#searchInput);
		this.addChild(new Spacer(1));

		this.#listContainer = new Container();
		this.addChild(this.#listContainer);
		this.addChild(new Spacer(1));
		this.addChild(new DynamicBorder());

		this.#load().then(() => {
			this.#filter(this.#searchInput.getValue());
			this.tui.requestRender();
		});
	}

	get focused(): boolean {
		return this.#focused;
	}

	set focused(value: boolean) {
		this.#focused = value;
		this.#searchInput.focused = value;
	}

	async #load(): Promise<void> {
		try {
			const state = await this.loadModels();
			this.#allModels = this.#sortModels(state.models);
			this.#filteredModels = this.#allModels;
		} catch (error) {
			this.#allModels = [];
			this.#filteredModels = [];
			this.#errorMessage = error instanceof Error ? error.message : String(error);
		}
	}

	#sortModels(models: ProviderModelItem[]): ProviderModelItem[] {
		return [...models].sort((a, b) => {
			const aDefault = a.roles.includes("default");
			const bDefault = b.roles.includes("default");
			if (aDefault && !bDefault) return -1;
			if (!aDefault && bDefault) return 1;
			const provider = a.providerName.localeCompare(b.providerName);
			return provider !== 0 ? provider : a.modelId.localeCompare(b.modelId);
		});
	}

	#filter(query: string): void {
		this.#filteredModels = query
			? fuzzyFilter(
					this.#allModels,
					query,
					model => `${model.providerName} ${model.providerType} ${model.modelId} ${model.providerName}/${model.modelId}`,
				)
			: this.#allModels;
		this.#selectedIndex = Math.min(this.#selectedIndex, Math.max(0, this.#filteredModels.length - 1));
		this.#updateList();
	}

	#updateList(): void {
		this.#listContainer.clear();

		const maxVisible = 10;
		const startIndex = Math.max(
			0,
			Math.min(this.#selectedIndex - Math.floor(maxVisible / 2), this.#filteredModels.length - maxVisible),
		);
		const endIndex = Math.min(startIndex + maxVisible, this.#filteredModels.length);

		for (let i = startIndex; i < endIndex; i++) {
			const item = this.#filteredModels[i];
			if (!item) continue;
			const selected = i === this.#selectedIndex;
			const prefix = selected ? theme.fg("accent", "-> ") : "  ";
			const modelText = selected ? theme.fg("accent", item.modelId) : theme.fg("text", item.modelId);
			const providerBadge = theme.fg("muted", `[${item.providerName}]`);
			const roles = item.roles.length > 0 ? ` ${theme.fg("success", item.roles.map(role => `@${role}`).join(" "))}` : "";
			this.#listContainer.addChild(new Text(`${prefix}${modelText} ${providerBadge}${roles}`, 0, 0));
		}

		if (startIndex > 0 || endIndex < this.#filteredModels.length) {
			this.#listContainer.addChild(
				new Text(theme.fg("muted", `  (${this.#selectedIndex + 1}/${this.#filteredModels.length})`), 0, 0),
			);
		}

		if (this.#errorMessage) {
			for (const line of this.#errorMessage.split("\n")) {
				this.#listContainer.addChild(new Text(theme.fg("error", line), 0, 0));
			}
		} else if (this.#filteredModels.length === 0) {
			this.#listContainer.addChild(new Text(theme.fg("muted", "  No models configured"), 0, 0));
		} else {
			const selected = this.#filteredModels[this.#selectedIndex];
			this.#listContainer.addChild(new Spacer(1));
			this.#listContainer.addChild(new Text(theme.fg("muted", `  Name: ${selected.displayName}`), 0, 0));
			this.#listContainer.addChild(
				new Text(theme.fg("muted", `  Provider: ${selected.providerName} (Type: ${selected.providerType})`), 0, 0),
			);
			const contextWindow = formatContextWindow(selected.contextWindow);
			this.#listContainer.addChild(new Text(theme.fg("muted", `  Context: ${contextWindow}`), 0, 0));
			this.#listContainer.addChild(new Text(`  ${theme.fg("muted", "Roles:")} ${formatRoles(selected.roles)}`, 0, 0));
		}
	}

	handleInput(keyData: string): void {
		const kb = getKeybindings();
		if (kb.matches(keyData, "tui.select.up")) {
			if (this.#filteredModels.length === 0) return;
			this.#selectedIndex = this.#selectedIndex === 0 ? this.#filteredModels.length - 1 : this.#selectedIndex - 1;
			this.#updateList();
			return;
		}
		if (kb.matches(keyData, "tui.select.down")) {
			if (this.#filteredModels.length === 0) return;
			this.#selectedIndex = this.#selectedIndex === this.#filteredModels.length - 1 ? 0 : this.#selectedIndex + 1;
			this.#updateList();
			return;
		}
		if (kb.matches(keyData, "tui.select.confirm")) {
			const selected = this.#filteredModels[this.#selectedIndex];
			if (selected) void this.onSelect(selected);
			return;
		}
		if (kb.matches(keyData, "tui.select.cancel")) {
			this.onCancel();
			return;
		}
		this.#searchInput.handleInput(keyData);
		this.#filter(this.#searchInput.getValue());
	}

	dispose(): void {}
}

function formatContextWindow(value: number | null | undefined): string {
	if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return "";
	return `${formatCompactNumber(value)} tokens`;
}

function formatRoles(roles: string[]): string {
	if (roles.length === 0) return theme.fg("muted", "<none>");
	return theme.fg("success", roles.map(role => `@${role}`).join(" "));
}
