// @ts-nocheck
export interface StatusLineOptions {
	icon?: string;
	title?: string;
	status?: string;
	meta?: string[];
}

export function renderStatusLine(options: StatusLineOptions): string {
	return [options.icon, options.title, options.status, ...(options.meta ?? [])].filter(Boolean).join(" ");
}

export function renderCodeCell(input: unknown): string[] {
	return [String(input ?? "")];
}
