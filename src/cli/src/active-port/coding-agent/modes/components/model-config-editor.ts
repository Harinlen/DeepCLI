// @ts-nocheck
import { Container, CURSOR_MARKER, extractPrintableText, matchesKey, Spacer, Text, type Focusable, type TUI } from "@/tui/index.js";
import type { ProviderModelItem } from "@/models/service.js";
import { formatCompactNumber } from "@/compat/utils.js";
import { theme } from "../theme/theme";
import { matchesAppInterrupt } from "../utils/keybinding-matchers";
import { DynamicBorder } from "./dynamic-border";

export interface ModelConfigUpdate {
	providerName: string;
	providerType: string;
	apiKey?: string | null;
	baseUrl?: string | null;
	awsRegion?: string | null;
	awsSecretKey?: string | null;
	displayName: string | null;
	modelId: string;
	contextWindow: number | null;
	roles: string[];
}

export interface ProviderTypeOption {
	type: string;
	settingFields: string[];
	effectiveBaseUrl?: string | null;
}

export interface ModelConfigEditorOptions {
	providerEditable?: boolean;
	initialFieldIndex?: number;
}

const ROLE_ORDER = ["default", "compact", "memory", "bash_judge", "embedding"];
const LABEL_WIDTH = 16;
const ROLE_FIELD_INDEX = 9;
const PROVIDER_FIELD_INDICES: Record<string, number> = {
	api_key: 2,
	base_url: 3,
	aws_region: 4,
	aws_secret_key: 5,
};

export class ModelConfigEditorComponent extends Container implements Focusable {
	#focused = false;
	#fieldIndex = 0;
	#roleIndex = 0;
	#error: string | undefined;
	#providerName: string;
	#providerType: string;
	#providerTypeOptions: ProviderTypeOption[];
	#providerValuesByType = new Map<string, ProviderFieldValues>();
	#apiKey = "";
	#baseUrl: string;
	#awsRegion: string;
	#awsSecretKey = "";
	#displayName: string;
	#modelId: string;
	#contextWindow: string;
	#roles: Set<string>;

	constructor(
		private readonly tui: TUI,
		private readonly model: ProviderModelItem,
		private readonly providerModelCount: number,
		providerTypeOptions: ProviderTypeOption[],
		private readonly options: ModelConfigEditorOptions = {},
		private readonly onSave: (update: ModelConfigUpdate) => void | Promise<void>,
		private readonly onCancel: () => void,
	) {
		super();
		this.#displayName = model.displayName === model.modelId ? "" : model.displayName;
		this.#providerName = model.providerName;
		this.#providerType = model.providerType;
		this.#providerTypeOptions = normalizeProviderTypeOptions(providerTypeOptions, model);
		this.#apiKey = model.providerApiKeyDisplay ?? "";
		this.#baseUrl = model.providerBaseUrl ?? "";
		this.#awsRegion = model.providerAwsRegion ?? "";
		this.#awsSecretKey = model.providerAwsSecretKeyDisplay ?? "";
		this.#saveProviderFieldValues(model.providerType);
		this.#modelId = model.modelId;
		this.#contextWindow = model.contextWindow ? formatCompactNumber(model.contextWindow) : "";
		this.#roles = new Set(model.roles);
		this.#fieldIndex = options.initialFieldIndex ?? (this.#providerEditable() ? 0 : 6);
		this.#renderBody();
	}

	get focused(): boolean {
		return this.#focused;
	}

	set focused(value: boolean) {
		if (this.#focused === value) return;
		this.#focused = value;
		this.#renderBody();
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
			this.#fieldIndex = this.#previousFieldIndex();
			this.#renderBody();
			return;
		}
		if (matchesKey(keyData, "down") || matchesKey(keyData, "tab")) {
			this.#fieldIndex = this.#nextFieldIndex();
			this.#renderBody();
			return;
		}
		if (this.#fieldIndex === ROLE_FIELD_INDEX) {
			this.#handleRolesInput(keyData);
			return;
		}
		if (this.#fieldIndex === 1) {
			this.#handleProviderTypeInput(keyData);
			return;
		}
		this.#handleTextInput(keyData);
	}

	#renderBody(): void {
		this.clear();
		this.addChild(new DynamicBorder());
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("accent", "  Provider Settings"), 0, 0));
		if (this.#providerEditable()) {
			this.#addValueField("Name:", fieldValue(this.#providerName, "<empty>", this.#focused && this.#fieldIndex === 0), 0);
			this.#addValueField("Type:", this.#formatProviderType(), 1);
		} else {
			this.#addStaticField("Name:", this.#providerName);
			this.#addStaticField("Type:", this.#providerType);
		}
		if (this.#showsProviderField("api_key")) {
			const value = secretValue(this.#apiKey, this.#activeApiKeyDisplay(), this.#activeHasApiKey(), this.#focused && this.#fieldIndex === 2);
			if (this.#providerEditable()) this.#addValueField("API key:", value, 2);
			else this.#addStaticField("API key:", this.#apiKey || this.#activeApiKeyDisplay() || (this.#activeHasApiKey() ? "<configured>" : "<empty>"));
		}
		if (this.#showsProviderField("base_url")) {
			if (this.#providerEditable()) this.#addValueField("Base URL:", fieldValue(this.#baseUrl, this.#activeProviderEffectiveBaseUrl() ?? "<default>", this.#focused && this.#fieldIndex === 3), 3);
			else this.#addStaticField("Base URL:", this.#baseUrl || this.#activeProviderEffectiveBaseUrl() || "<default>");
		}
		if (this.#showsProviderField("aws_region")) {
			if (this.#providerEditable()) this.#addValueField("AWS region:", fieldValue(this.#awsRegion, "<none>", this.#focused && this.#fieldIndex === 4), 4);
			else this.#addStaticField("AWS region:", this.#awsRegion || "<none>");
		}
		if (this.#showsProviderField("aws_secret_key")) {
			const value = secretValue(this.#awsSecretKey, this.#activeAwsSecretKeyDisplay(), this.#activeHasAwsSecretKey(), this.#focused && this.#fieldIndex === 5);
			if (this.#providerEditable()) this.#addValueField("AWS secret:", value, 5);
			else this.#addStaticField("AWS secret:", this.#awsSecretKey || this.#activeAwsSecretKeyDisplay() || (this.#activeHasAwsSecretKey() ? "<configured>" : "<empty>"));
		}
		this.addChild(
			new Text(`   ${theme.fg("muted", "Other models:".padEnd(LABEL_WIDTH))}${theme.fg("muted", formatOtherModels(this.providerModelCount))}`, 0, 0),
		);
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("accent", "  Model Settings"), 0, 0));
		this.#addValueField("Name:", fieldValue(this.#displayName, "<empty>", this.#focused && this.#fieldIndex === 6), 6);
		this.#addValueField("Model ID:", fieldValue(this.#modelId, "<empty>", this.#focused && this.#fieldIndex === 7), 7);
		this.#addValueField("Context tokens:", fieldValue(this.#contextWindow, "<default>", this.#focused && this.#fieldIndex === 8), 8);
		this.#addValueField("Roles:", this.#formatRoles(), ROLE_FIELD_INDEX);
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

	#visibleFieldIndices(): number[] {
		if (!this.#providerEditable()) return [6, 7, 8, ROLE_FIELD_INDEX];
		const providerFields = Object.entries(PROVIDER_FIELD_INDICES)
			.filter(([field]) => this.#showsProviderField(field))
			.map(([, index]) => index);
		return [0, 1, ...providerFields, 6, 7, 8, ROLE_FIELD_INDEX];
	}

	#nextFieldIndex(): number {
		const fields = this.#visibleFieldIndices();
		const current = fields.indexOf(this.#fieldIndex);
		if (current < 0) return fields[0] ?? 0;
		return fields[Math.min(fields.length - 1, current + 1)] ?? this.#fieldIndex;
	}

	#previousFieldIndex(): number {
		const fields = this.#visibleFieldIndices();
		const current = fields.indexOf(this.#fieldIndex);
		if (current < 0) return fields[0] ?? 0;
		return fields[Math.max(0, current - 1)] ?? this.#fieldIndex;
	}

	#showsProviderField(field: string): boolean {
		return this.#activeProviderSettingFields().includes(field);
	}

	#activeProviderSettingFields(): string[] {
		return this.#providerTypeOptions.find(option => option.type === this.#providerType)?.settingFields ?? this.model.providerSettingFields;
	}

	#activeProviderEffectiveBaseUrl(): string | null | undefined {
		return this.#providerTypeOptions.find(option => option.type === this.#providerType)?.effectiveBaseUrl ?? this.model.providerEffectiveBaseUrl;
	}

	#activeApiKeyDisplay(): string | null | undefined {
		return this.#providerType === this.model.providerType ? this.model.providerApiKeyDisplay : undefined;
	}

	#activeHasApiKey(): boolean {
		return this.#providerType === this.model.providerType && this.model.providerHasApiKey;
	}

	#activeAwsSecretKeyDisplay(): string | null | undefined {
		return this.#providerType === this.model.providerType ? this.model.providerAwsSecretKeyDisplay : undefined;
	}

	#activeHasAwsSecretKey(): boolean {
		return this.#providerType === this.model.providerType && this.model.providerHasAwsSecretKey;
	}

	#formatProviderType(): string {
		const selected = this.#focused && this.#fieldIndex === 1;
		const text = this.#providerType.trim();
		const value = text ? theme.fg("text", text) : theme.fg("muted", "<empty>");
		if (!selected) return value;
		if (this.#providerTypeOptions.length <= 1) return value;
		return `${theme.fg("dim", "< ")}${value}${theme.fg("dim", " >")}`;
	}

	#providerEditable(): boolean {
		return this.options.providerEditable !== false;
	}

	#formatRoles(): string {
		return ROLE_ORDER.map((role, index) => {
			const checked = this.#roles.has(role) ? "[x]" : "[ ]";
			const text = `${checked} ${role}`;
			if (this.#fieldIndex === ROLE_FIELD_INDEX && this.#roleIndex === index) return theme.fg("accent", text);
			if (this.#roles.has(role)) return theme.fg("success", text);
			return theme.fg("muted", text);
		}).join(" ");
	}

	#handleTextInput(keyData: string): void {
		if (matchesKey(keyData, "backspace")) {
			this.#setActiveText(this.#activeText().slice(0, -1));
			this.#renderBody();
			return;
		}
		const printable = extractPrintableText(keyData);
		if (!printable) return;
		this.#setActiveText(this.#activeText() + printable);
		this.#renderBody();
	}

	#activeText(): string {
		if (this.#fieldIndex === 0) return this.#providerName;
		if (this.#fieldIndex === 1) return this.#providerType;
		if (this.#fieldIndex === 2) return this.#apiKey;
		if (this.#fieldIndex === 3) return this.#baseUrl;
		if (this.#fieldIndex === 4) return this.#awsRegion;
		if (this.#fieldIndex === 5) return this.#awsSecretKey;
		if (this.#fieldIndex === 6) return this.#displayName;
		if (this.#fieldIndex === 7) return this.#modelId;
		if (this.#fieldIndex === 8) return this.#contextWindow;
		return "";
	}

	#setActiveText(value: string): void {
		if (this.#fieldIndex === 0) this.#providerName = value;
		if (this.#fieldIndex === 1) this.#providerType = value;
		if (this.#fieldIndex === 2) this.#apiKey = value;
		if (this.#fieldIndex === 3) this.#baseUrl = value;
		if (this.#fieldIndex === 4) this.#awsRegion = value;
		if (this.#fieldIndex === 5) this.#awsSecretKey = value;
		if (this.#fieldIndex === 6) this.#displayName = value;
		if (this.#fieldIndex === 7) this.#modelId = value;
		if (this.#fieldIndex === 8) this.#contextWindow = value;
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

	#handleProviderTypeInput(keyData: string): void {
		if (this.#providerTypeOptions.length <= 1) return;
		if (matchesKey(keyData, "left")) {
			this.#selectProviderType(-1);
			return;
		}
		if (matchesKey(keyData, "right") || matchesKey(keyData, "space") || keyData === " ") {
			this.#selectProviderType(1);
		}
	}

	#selectProviderType(delta: number): void {
		const current = this.#providerTypeOptions.findIndex(option => option.type === this.#providerType);
		const index = current < 0 ? 0 : current;
		const next = (index + delta + this.#providerTypeOptions.length) % this.#providerTypeOptions.length;
		this.#saveProviderFieldValues(this.#providerType);
		this.#providerType = this.#providerTypeOptions[next]?.type ?? this.#providerType;
		this.#restoreProviderFieldValues(this.#providerType);
		this.#renderBody();
	}

	#saveProviderFieldValues(providerType: string): void {
		this.#providerValuesByType.set(providerType, {
			apiKey: this.#apiKey,
			baseUrl: this.#baseUrl,
			awsRegion: this.#awsRegion,
			awsSecretKey: this.#awsSecretKey,
		});
	}

	#restoreProviderFieldValues(providerType: string): void {
		const values = this.#providerValuesByType.get(providerType);
		this.#apiKey = values?.apiKey ?? "";
		this.#baseUrl = values?.baseUrl ?? "";
		this.#awsRegion = values?.awsRegion ?? "";
		this.#awsSecretKey = values?.awsSecretKey ?? "";
	}

	async #save(): Promise<void> {
		try {
			this.#error = undefined;
			const providerName = this.#providerName.trim();
			const providerType = this.#providerType.trim();
			const modelId = this.#modelId.trim();
			if (!providerName) throw new Error("Provider must not be empty");
			if (!providerType) throw new Error("Type must not be empty");
			if (!modelId) throw new Error("Model ID must not be empty");
			const update: ModelConfigUpdate = {
				providerName,
				providerType,
				displayName: normalizeDisplayName(this.#displayName, this.model),
				modelId,
				contextWindow: parseContextWindow(this.#contextWindow),
				roles: parseRoles(this.#roles),
			};
			if (this.#providerEditable() && this.#showsProviderField("api_key")) update.apiKey = optionalSecret(this.#apiKey);
			if (this.#providerEditable() && this.#showsProviderField("base_url")) update.baseUrl = optionalText(this.#baseUrl);
			if (this.#providerEditable() && this.#showsProviderField("aws_region")) update.awsRegion = optionalText(this.#awsRegion);
			if (this.#providerEditable() && this.#showsProviderField("aws_secret_key")) update.awsSecretKey = optionalSecret(this.#awsSecretKey);
			await this.onSave(update);
		} catch (error) {
			this.#error = error instanceof Error ? error.message : String(error);
			this.#renderBody();
		}
	}

	dispose(): void {}
}

interface ProviderFieldValues {
	apiKey: string;
	baseUrl: string;
	awsRegion: string;
	awsSecretKey: string;
}

function fieldValue(value: string, emptyLabel: string, showCursor: boolean): string {
	const text = value.trim();
	if (text) return `${theme.fg("text", text)}${showCursor ? CURSOR_MARKER : ""}`;
	return `${theme.fg("muted", emptyLabel)}${showCursor ? CURSOR_MARKER : ""}`;
}

function secretValue(value: string, displayValue: string | null | undefined, configured: boolean, showCursor: boolean): string {
	const text = value.trim() || displayValue?.trim() || "";
	if (text) return `${theme.fg("text", text)}${showCursor ? CURSOR_MARKER : ""}`;
	return `${theme.fg("muted", configured ? "<configured>" : "<empty>")}${showCursor ? CURSOR_MARKER : ""}`;
}

function normalizeProviderTypeOptions(options: ProviderTypeOption[], model: ProviderModelItem): ProviderTypeOption[] {
	const byType = new Map<string, ProviderTypeOption>();
	for (const option of options) {
		const type = option.type.trim();
		if (!type || byType.has(type)) continue;
		byType.set(type, { type, settingFields: [...option.settingFields], effectiveBaseUrl: option.effectiveBaseUrl });
	}
	if (!byType.has(model.providerType)) {
		byType.set(model.providerType, {
			type: model.providerType,
			settingFields: [...model.providerSettingFields],
			effectiveBaseUrl: model.providerEffectiveBaseUrl,
		});
	}
	return [...byType.values()].sort((a, b) => a.type.localeCompare(b.type));
}

function formatOtherModels(providerModelCount: number): string {
	const otherCount = Math.max(0, providerModelCount - 1);
	return otherCount === 1 ? "1 other" : `${otherCount} others`;
}

function optionalText(value: string): string {
	return value.trim();
}

function optionalSecret(value: string): string | null {
	const text = value.trim();
	return text || null;
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
