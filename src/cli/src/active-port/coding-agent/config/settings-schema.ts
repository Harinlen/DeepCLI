// -nocheck
export type SettingPath = string;
export type SettingValue = unknown;
export type GroupPrefix = string;
export type BashInterceptorRule = Record<string, unknown>;
export type GroupTypeMap = Record<string, unknown>;

export const SETTINGS_SCHEMA: Record<string, unknown> = {};

export function getDefault(path: string): unknown {
	const defaults: Record<string, unknown> = {
		"tui.maxInlineImageColumns": 80,
		"tui.maxInlineImageRows": 24,
	};
	return defaults[path];
}
