// @ts-nocheck
import {
	BracketedPasteHandler,
	Container,
	CURSOR_MARKER,
	extractPrintableText,
	matchesKey,
	replaceTabs,
	Spacer,
	Text,
	type Focusable,
	type TUI,
} from "@/tui/index.js";
import { theme } from "../theme/theme";
import { matchesAppInterrupt } from "../utils/keybinding-matchers";
import { DynamicBorder } from "./dynamic-border";

export interface WebFetchConfigField {
	path: string;
	label: string;
	kind: "secret" | "value";
	value?: unknown;
	status?: "configured" | "missing";
}

export class WebFetchConfigEditorComponent extends Container implements Focusable {
	#focused = false;
	#fieldIndex = 0;
	#error: string | undefined;
	#pasteHandler = new BracketedPasteHandler();
	#values: string[];
	#initialValues: string[];

	constructor(
		private readonly tui: TUI,
		private readonly title: string,
		private readonly fields: WebFetchConfigField[],
		private readonly onSave: (updates: Array<{ path: string; value: unknown; kind: WebFetchConfigField["kind"] }>) => void | Promise<void>,
		private readonly onCancel: () => void,
	) {
		super();
		this.#values = fields.map(field => field.kind === "secret" ? "" : stringifyValue(field.value));
		this.#initialValues = [...this.#values];
		this.#renderBody();
	}

	get focused(): boolean {
		return this.#focused;
	}

	set focused(value: boolean) {
		this.#focused = value;
		this.#renderBody();
	}

	handleInput(keyData: string): void {
		const paste = this.#pasteHandler.process(keyData);
		if (paste.handled) {
			if (paste.pasteContent !== undefined) {
				this.#appendText(paste.pasteContent);
				if (paste.remaining.length > 0) this.handleInput(paste.remaining);
			}
			return;
		}
		if (matchesAppInterrupt(keyData)) {
			this.onCancel();
			return;
		}
		if (matchesKey(keyData, "enter") || matchesKey(keyData, "return") || keyData === "\n" || matchesKey(keyData, "ctrl+s")) {
			void this.#save();
			return;
		}
		if (matchesKey(keyData, "up")) {
			this.#fieldIndex = Math.max(0, this.#fieldIndex - 1);
			this.#renderBody();
			return;
		}
		if (matchesKey(keyData, "down") || matchesKey(keyData, "tab")) {
			this.#fieldIndex = Math.min(this.fields.length - 1, this.#fieldIndex + 1);
			this.#renderBody();
			return;
		}
		if (matchesKey(keyData, "backspace")) {
			this.#values[this.#fieldIndex] = (this.#values[this.#fieldIndex] ?? "").slice(0, -1);
			this.#renderBody();
			return;
		}
		const printable = extractPrintableText(keyData);
		if (!printable) return;
		this.#values[this.#fieldIndex] = `${this.#values[this.#fieldIndex] ?? ""}${printable}`;
		this.#renderBody();
	}

	#appendText(text: string): void {
		const clean = replaceTabs(text.replace(/\r\n/g, "").replace(/\r/g, "").replace(/\n/g, ""));
		if (!clean) return;
		this.#values[this.#fieldIndex] = `${this.#values[this.#fieldIndex] ?? ""}${clean}`;
		this.#renderBody();
	}

	#renderBody(): void {
		this.clear();
		this.addChild(new DynamicBorder());
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("accent", `  ${this.title}`), 0, 0));
		this.addChild(new Spacer(1));
		for (let i = 0; i < this.fields.length; i++) {
			const field = this.fields[i];
			const selected = i === this.#fieldIndex;
			const prefix = selected ? theme.fg("accent", "-> ") : "   ";
			const label = selected ? theme.fg("accent", field.label.padEnd(18)) : theme.fg("muted", field.label.padEnd(18));
			this.addChild(new Text(`${prefix}${label}${this.#formatValue(field, i, selected)}`, 0, 0));
		}
		if (this.#error) {
			this.addChild(new Spacer(1));
			this.addChild(new Text(theme.fg("error", `  ${this.#error}`), 0, 0));
		}
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("dim", "  <↑/↓> field  type to edit  <Enter> save  <Esc> cancel"), 0, 0));
		this.addChild(new Spacer(1));
		this.addChild(new DynamicBorder());
		this.tui.requestRender();
	}

	#formatValue(field: WebFetchConfigField, index: number, selected: boolean): string {
		const value = this.#values[index] ?? "";
		if (field.kind === "secret") {
			if (value.trim()) return `${theme.fg("text", value)}${selected ? CURSOR_MARKER : ""}`;
			return `${theme.fg("muted", field.status === "configured" ? "<configured>" : "<empty>")}${selected ? CURSOR_MARKER : ""}`;
		}
		if (value.trim()) return `${theme.fg("text", value)}${selected ? CURSOR_MARKER : ""}`;
		return `${theme.fg("muted", "<empty>")}${selected ? CURSOR_MARKER : ""}`;
	}

	async #save(): Promise<void> {
		try {
			this.#error = undefined;
			const updates: Array<{ path: string; value: unknown; kind: WebFetchConfigField["kind"] }> = [];
			for (let i = 0; i < this.fields.length; i++) {
				const field = this.fields[i];
				const value = (this.#values[i] ?? "").trim();
				if (field.kind === "secret") {
					if (value) updates.push({ path: field.path, value, kind: field.kind });
					continue;
				}
				if (value !== this.#initialValues[i]) {
					updates.push({ path: field.path, value: parseConfigValue(value), kind: field.kind });
				}
			}
			if (updates.length === 0) {
				this.onCancel();
				return;
			}
			await this.onSave(updates);
		} catch (error) {
			this.#error = error instanceof Error ? error.message : String(error);
			this.#renderBody();
		}
	}
}

function stringifyValue(value: unknown): string {
	if (value === null || value === undefined) return "";
	return String(value);
}

function parseConfigValue(value: string): unknown {
	const trimmed = value.trim();
	if (trimmed === "true") return true;
	if (trimmed === "false") return false;
	if (/^-?\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed);
	return trimmed;
}
