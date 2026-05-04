// @ts-nocheck
import { Container, CURSOR_MARKER, extractPrintableText, matchesKey, Spacer, Text, type Focusable, type TUI } from "@/tui/index.js";
import type { ProviderModelItem } from "@/models/service.js";
import { formatCompactNumber } from "@/compat/utils.js";
import { theme } from "../theme/theme";
import { matchesAppInterrupt } from "../utils/keybinding-matchers";
import { DynamicBorder } from "./dynamic-border";

export interface ModelConfigUpdate {
	displayName: string | null;
	contextWindow: number | null;
	roles: string[];
}

const ROLE_ORDER = ["default", "compact", "memory", "bash_judge", "embedding"];
const LABEL_WIDTH = 16;

export class ModelConfigEditorComponent extends Container implements Focusable {
	#focused = false;
	#fieldIndex = 0;
	#roleIndex = 0;
	#error: string | undefined;
	#displayName: string;
	#contextWindow: string;
	#roles: Set<string>;

	constructor(
		private readonly tui: TUI,
		private readonly model: ProviderModelItem,
		private readonly providerModelCount: number,
		private readonly onSave: (update: ModelConfigUpdate) => void | Promise<void>,
		private readonly onCancel: () => void,
	) {
		super();
		this.#displayName = model.displayName === model.modelId ? "" : model.displayName;
		this.#contextWindow = model.contextWindow ? formatCompactNumber(model.contextWindow) : "";
		this.#roles = new Set(model.roles);
		this.#renderBody();
	}

	get focused(): boolean {
		return this.#focused;
	}

	set focused(value: boolean) {
		this.#focused = value;
	}

	handleInput(keyData: string): void {
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
			this.#fieldIndex = Math.min(2, this.#fieldIndex + 1);
			this.#renderBody();
			return;
		}
		if (this.#fieldIndex === 2) {
			this.#handleRolesInput(keyData);
			return;
		}
		this.#handleTextInput(keyData);
	}

	#renderBody(): void {
		this.clear();
		this.addChild(new DynamicBorder());
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("accent", "  Provider Settings"), 0, 0));
		this.addChild(new Text(theme.fg("muted", `  Provider: ${this.model.providerName}`), 0, 0));
		this.addChild(new Text(theme.fg("muted", `  Type: ${this.model.providerType}`), 0, 0));
		this.addChild(new Text(theme.fg("muted", `  Models: ${this.providerModelCount} configured`), 0, 0));
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("accent", "  Model Settings"), 0, 0));
		this.#addValueField("Name:", fieldValue(this.#displayName, "<empty>", this.#focused && this.#fieldIndex === 0), 0);
		this.#addStaticField("Model ID:", this.model.modelId);
		this.#addValueField("Context tokens:", fieldValue(this.#contextWindow, "<default>", this.#focused && this.#fieldIndex === 1), 1);
		this.#addValueField("Roles:", this.#formatRoles(), 2);
		if (this.#error) {
			this.addChild(new Spacer(1));
			this.addChild(new Text(theme.fg("error", `  ${this.#error}`), 0, 0));
		}
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("dim", "  <↑/↓> field  <←/→> role  <Space> toggle  <Enter> save  <Esc> cancel"), 0, 0));
		this.addChild(new Spacer(1));
		this.addChild(new DynamicBorder());
		this.tui.requestRender();
	}

	#addValueField(label: string, value: string, index: number): void {
		const selected = index === this.#fieldIndex;
		const prefix = selected ? theme.fg("accent", "-> ") : "   ";
		const labelText = selected ? theme.fg("accent", label.padEnd(LABEL_WIDTH)) : theme.fg("muted", label.padEnd(LABEL_WIDTH));
		this.addChild(new Text(`${prefix}${labelText}${value}`, 0, 0));
	}

	#addStaticField(label: string, value: string): void {
		this.addChild(new Text(`   ${theme.fg("muted", label.padEnd(LABEL_WIDTH))}${theme.fg("muted", value)}`, 0, 0));
	}

	#formatRoles(): string {
		return ROLE_ORDER.map((role, index) => {
			const checked = this.#roles.has(role) ? "[x]" : "[ ]";
			const text = `${checked} ${role}`;
			if (this.#fieldIndex === 2 && this.#roleIndex === index) return theme.fg("accent", text);
			if (this.#roles.has(role)) return theme.fg("success", text);
			return theme.fg("muted", text);
		}).join(" ");
	}

	#handleTextInput(keyData: string): void {
		if (matchesKey(keyData, "backspace")) {
			if (this.#fieldIndex === 0) this.#displayName = this.#displayName.slice(0, -1);
			if (this.#fieldIndex === 1) this.#contextWindow = this.#contextWindow.slice(0, -1);
			this.#renderBody();
			return;
		}
		const printable = extractPrintableText(keyData);
		if (!printable) return;
		if (this.#fieldIndex === 0) this.#displayName += printable;
		if (this.#fieldIndex === 1) this.#contextWindow += printable;
		this.#renderBody();
	}

	#handleRolesInput(keyData: string): void {
		if (matchesKey(keyData, "left")) {
			this.#roleIndex = this.#roleIndex === 0 ? ROLE_ORDER.length - 1 : this.#roleIndex - 1;
			this.#renderBody();
			return;
		}
		if (matchesKey(keyData, "right")) {
			this.#roleIndex = this.#roleIndex === ROLE_ORDER.length - 1 ? 0 : this.#roleIndex + 1;
			this.#renderBody();
			return;
		}
		if (matchesKey(keyData, "space") || keyData === " ") {
			const role = ROLE_ORDER[this.#roleIndex];
			if (this.#roles.has(role)) this.#roles.delete(role);
			else this.#roles.add(role);
			this.#renderBody();
		}
	}

	async #save(): Promise<void> {
		try {
			this.#error = undefined;
			await this.onSave({
				displayName: normalizeDisplayName(this.#displayName, this.model),
				contextWindow: parseContextWindow(this.#contextWindow),
				roles: parseRoles(this.#roles),
			});
		} catch (error) {
			this.#error = error instanceof Error ? error.message : String(error);
			this.#renderBody();
		}
	}

	dispose(): void {}
}

function fieldValue(value: string, emptyLabel: string, showCursor: boolean): string {
	const text = value.trim();
	if (text) return `${theme.fg("text", text)}${showCursor ? CURSOR_MARKER : ""}`;
	return `${theme.fg("muted", emptyLabel)}${showCursor ? CURSOR_MARKER : ""}`;
}

function normalizeDisplayName(value: string, model: ProviderModelItem): string | null {
	const text = value.trim();
	if (!text || text === model.modelId || text === `${model.providerName}/${model.modelId}`) return null;
	return text;
}

function parseContextWindow(value: string): number | null {
	const text = value.trim();
	if (!text) return null;
	const match = text.match(/^(\d+(?:\.\d+)?)([kKmM])?$/);
	if (!match) throw new Error("Context must be a number, K, or M value");
	const amount = Number(match[1]);
	const suffix = match[2]?.toLowerCase();
	const multiplier = suffix === "m" ? 1_000_000 : suffix === "k" ? 1_000 : 1;
	const tokens = Math.round(amount * multiplier);
	if (!Number.isFinite(tokens) || tokens <= 0) throw new Error("Context must be positive");
	return tokens;
}

function parseRoles(roles: Set<string>): string[] {
	return ROLE_ORDER.filter(role => roles.has(role));
}

export function formatModelContext(value: number | null | undefined): string {
	if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return "";
	return `${formatCompactNumber(value)} tokens`;
}
