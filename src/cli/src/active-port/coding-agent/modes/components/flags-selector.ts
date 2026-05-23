// @ts-nocheck
import { Container, type Focusable, fuzzyFilter, getKeybindings, Input, Spacer, Text, type TUI } from "@/tui/index.js";
import { theme } from "../theme/theme";
import { DynamicBorder } from "./dynamic-border";

export type FlagSectionState = {
	section?: unknown;
	payload?: unknown;
	revision?: unknown;
	pendingRestart?: unknown;
};

export type FlagItemState = {
	section: string;
	key: string;
	value: unknown;
	revision?: number;
	pendingRestart: boolean;
};

export class FlagsSelectorComponent extends Container implements Focusable {
	#searchInput: Input;
	#listContainer: Container;
	#allItems: FlagItemState[] = [];
	#filteredItems: FlagItemState[] = [];
	#selectedIndex = 0;
	#errorMessage: string | undefined;
	#focused = false;

	constructor(
		private readonly tui: TUI,
		private readonly loadFlags: () => Promise<{ sections?: FlagSectionState[] }>,
		private readonly onEdit: (item: FlagItemState) => void | Promise<void>,
		private readonly onReset: (item: FlagItemState) => void | Promise<void>,
		private readonly onCancel: () => void,
		initialSearchInput?: string,
	) {
		super();

		this.addChild(new DynamicBorder());
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("muted", "Enter toggles booleans or edits a value. Press r to reset the selected flag."), 0, 0));
		this.addChild(new Spacer(1));

		this.#searchInput = new Input();
		this.#searchInput.onSubmit = () => {
			const selected = this.#filteredItems[this.#selectedIndex];
			if (selected) void this.onEdit(selected);
		};
		this.#searchInput.onEscape = () => this.onCancel();
		if (initialSearchInput) this.#searchInput.setValue(initialSearchInput);
		this.addChild(this.#searchInput);
		this.addChild(new Spacer(1));

		this.#listContainer = new Container();
		this.addChild(this.#listContainer);
		this.addChild(new Spacer(1));
		this.addChild(new DynamicBorder());

		this.reload().catch(() => undefined);
	}

	get focused(): boolean {
		return this.#focused;
	}

	set focused(value: boolean) {
		this.#focused = value;
		this.#searchInput.focused = value;
	}

	async reload(select?: { section: string; key: string }): Promise<void> {
		try {
			const state = await this.loadFlags();
			this.#allItems = collectFlagItems(state);
			this.#errorMessage = undefined;
		} catch (error) {
			this.#allItems = [];
			this.#errorMessage = error instanceof Error ? error.message : String(error);
		}
		this.#filter(this.#searchInput.getValue());
		if (select) {
			const index = this.#filteredItems.findIndex(item => item.section === select.section && item.key === select.key);
			if (index >= 0) this.#selectedIndex = index;
			this.#updateList();
		}
		this.tui.requestRender();
	}

	#filter(query: string): void {
		this.#filteredItems = query
			? fuzzyFilter(this.#allItems, query, item => `${item.section}.${item.key} ${formatFlagValue(item.value)}`)
			: this.#allItems;
		this.#selectedIndex = Math.min(this.#selectedIndex, Math.max(0, this.#filteredItems.length - 1));
		this.#updateList();
	}

	#updateList(): void {
		this.#listContainer.clear();
		const maxVisible = 12;
		const startIndex = Math.max(
			0,
			Math.min(this.#selectedIndex - Math.floor(maxVisible / 2), this.#filteredItems.length - maxVisible),
		);
		const endIndex = Math.min(startIndex + maxVisible, this.#filteredItems.length);

		for (let i = startIndex; i < endIndex; i++) {
			const item = this.#filteredItems[i];
			if (!item) continue;
			const selected = i === this.#selectedIndex;
			const prefix = selected ? theme.fg("accent", "-> ") : "   ";
			const marker = flagMarker(item.value);
			const label = `${item.section}.${item.key}`.padEnd(30);
			const value = formatFlagValue(item.value).padEnd(8);
			const revision = item.revision === undefined ? "rev ?" : `rev ${item.revision}`;
			const restart = item.pendingRestart ? theme.fg("warning", "restart pending") : theme.fg("muted", "active");
			const text = selected ? theme.fg("accent", label) : theme.fg("text", label);
			this.#listContainer.addChild(new Text(`${prefix}${marker} ${text} ${value} ${theme.fg("muted", revision)}  ${restart}`, 0, 0));
		}

		if (startIndex > 0 || endIndex < this.#filteredItems.length) {
			this.#listContainer.addChild(new Text(theme.fg("muted", `   (${this.#selectedIndex + 1}/${this.#filteredItems.length})`), 0, 0));
		}

		if (this.#errorMessage) {
			for (const line of this.#errorMessage.split("\n")) {
				this.#listContainer.addChild(new Text(theme.fg("error", line), 0, 0));
			}
		} else if (this.#filteredItems.length === 0) {
			this.#listContainer.addChild(new Text(theme.fg("muted", "   No flags match."), 0, 0));
		}
	}

	handleInput(keyData: string): void {
		const kb = getKeybindings();
		if (kb.matches(keyData, "tui.select.up")) {
			if (this.#filteredItems.length === 0) return;
			this.#selectedIndex = this.#selectedIndex === 0 ? this.#filteredItems.length - 1 : this.#selectedIndex - 1;
			this.#updateList();
			this.tui.requestRender();
			return;
		}
		if (kb.matches(keyData, "tui.select.down")) {
			if (this.#filteredItems.length === 0) return;
			this.#selectedIndex = this.#selectedIndex === this.#filteredItems.length - 1 ? 0 : this.#selectedIndex + 1;
			this.#updateList();
			this.tui.requestRender();
			return;
		}
		if (kb.matches(keyData, "tui.select.confirm")) {
			const selected = this.#filteredItems[this.#selectedIndex];
			if (selected) void this.onEdit(selected);
			return;
		}
		if (keyData === "r" || keyData === "R") {
			const selected = this.#filteredItems[this.#selectedIndex];
			if (selected) void this.onReset(selected);
			return;
		}
		if (kb.matches(keyData, "tui.select.cancel")) {
			this.onCancel();
			return;
		}
		this.#searchInput.handleInput(keyData);
		this.#filter(this.#searchInput.getValue());
		this.tui.requestRender();
	}

	dispose(): void {}
}

export function collectFlagItems(state: { sections?: FlagSectionState[] } | undefined): FlagItemState[] {
	const sections = Array.isArray(state?.sections) ? state.sections : [];
	return sections.flatMap(section => {
		const name = String(section?.section ?? "unknown");
		const payload = section?.payload && typeof section.payload === "object" && !Array.isArray(section.payload)
			? section.payload as Record<string, unknown>
			: {};
		const revision = Number(section?.revision);
		return Object.keys(payload).sort().map(key => ({
			section: name,
			key,
			value: payload[key],
			pendingRestart: Boolean(section?.pendingRestart),
			...(Number.isFinite(revision) ? { revision } : {}),
		}));
	});
}

export function formatFlagValue(value: unknown): string {
	if (typeof value === "string") return value;
	if (typeof value === "number" || typeof value === "boolean") return String(value);
	return JSON.stringify(value);
}

function flagMarker(value: unknown): string {
	if (value === true) return theme.fg("success", "[x]");
	if (value === false) return theme.fg("muted", "[ ]");
	return theme.fg("muted", "[-]");
}
