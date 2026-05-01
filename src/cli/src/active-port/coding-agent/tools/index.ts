// -nocheck
export interface ExitPlanModeDetails {
	plan?: string;
	approved?: boolean;
}

export function isSearchProviderPreference(_value: unknown): boolean {
	return false;
}

export function setPreferredImageProvider(_value: unknown): void {}
export function setPreferredSearchProvider(_value: unknown): void {}

export interface LspStartupServerInfo {
	name: string;
	status?: string;
}

export interface EditToolDetails {
	filePath?: string;
	operation?: string;
}

export type ToolDetails = Record<string, unknown>;
