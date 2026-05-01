// -nocheck
export type SettingPath = string;
export type SettingValue = unknown;
export type Settings = SettingsStore;

const defaults: Record<string, unknown> = {
	"tui.maxInlineImageColumns": 80,
	"tui.maxInlineImageRows": 24,
	"tui.inlineImages": true,
	"theme.dark": "dark",
	"theme.light": "light",
	"theme.symbols": "unicode",
	"editor.custom": false,
	"editor.historySearch": true,
	"autocompleteMaxVisible": 8,
	"branchSummary.enabled": false,
	"clearOnShrink": true,
	"collapseChangelog": true,
	"completion.notify": "off",
	"cycleOrder": ["default"],
	"display.showTokenUsage": true,
	"doubleEscapeAction": "none",
	"edit.fuzzyMatch": true,
	"edit.fuzzyThreshold": 0.8,
	"hideThinkingBlock": false,
	"images.autoResize": false,
	"model.default": "",
	"providers.openaiWebsockets": false,
	"read.toolResultPreview": true,
	"showHardwareCursor": true,
	"skills.enableSkillCommands": false,
	"startup.quiet": false,
	"statusLine.enabled": true,
	"statusLine.preset": "default",
	"statusLine.leftSegments": undefined,
	"statusLine.rightSegments": undefined,
	"statusLine.separator": "arrow",
	"statusLine.segmentOptions": {},
	"statusLine.showHookStatus": true,
	"stt.enabled": false,
	"terminal.showImages": false,
	"treeFilterMode": "all",
	"compaction.enabled": true,
	"compaction.idleEnabled": false,
	"compaction.idleThresholdTokens": 0,
	"compaction.idleTimeoutSeconds": 300,
};

export class SettingsStore {
	#values = new Map<string, unknown>();

	constructor(overrides?: Record<string, unknown>) {
		for (const [key, value] of Object.entries(defaults)) this.#values.set(key, value);
		for (const [key, value] of Object.entries(overrides ?? {})) this.#values.set(key, value);
	}

	get<T = unknown>(key: string): T {
		return this.#values.get(key) as T;
	}

	set(key: string, value: unknown): void {
		this.#values.set(key, value);
	}

	has(key: string): boolean {
		return this.#values.has(key);
	}

	getGroup<T = Record<string, unknown>>(prefix: string): T {
		const group: Record<string, unknown> = {};
		const start = `${prefix}.`;
		for (const [key, value] of this.#values.entries()) {
			if (key.startsWith(start)) group[key.slice(start.length)] = value;
		}
		return group as T;
	}

	all(): Record<string, unknown> {
		return Object.fromEntries(this.#values.entries());
	}

	static isolated(overrides?: Record<string, unknown>): SettingsStore {
		return new SettingsStore(overrides);
	}
}

export const settings = new SettingsStore();
export const Settings = {
	init: async () => settings,
	get: <T = unknown>(key: string): T => settings.get<T>(key),
	set: (key: string, value: unknown) => settings.set(key, value),
	getGroup: <T = Record<string, unknown>>(prefix: string): T => settings.getGroup<T>(prefix),
};
