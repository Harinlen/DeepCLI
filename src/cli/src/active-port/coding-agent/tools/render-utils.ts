// -nocheck
import { replaceTabs, truncateToWidth, wrapTextWithAnsi } from "@/tui/index.js";
import { settings } from "../config/settings";

export { replaceTabs, truncateToWidth, wrapTextWithAnsi };

export const PREVIEW_LIMITS = { lines: 20, chars: 4000 };
export function formatBytes(bytes: number): string { return `${bytes} B`; }

export function resolveImageOptions(): { maxWidthCells: number; maxHeightCells?: number } {
	return {
		maxWidthCells: Number(settings.get("tui.maxInlineImageColumns") ?? 80),
		maxHeightCells: Number(settings.get("tui.maxInlineImageRows") ?? 24),
	};
}

export function formatExpandHint(_theme: unknown, expanded?: boolean, hasMore?: boolean): string {
	return !expanded && hasMore !== false ? "(Ctrl+O for more)" : "";
}

export function truncateToWidthWithTabs(text: string, width: number): string {
	return truncateToWidth(replaceTabs(text), width);
}

export function shortenPath(value: string, maxLength = 32): string {
	if (value.length <= maxLength) return value;
	return `…${value.slice(Math.max(0, value.length - maxLength + 1))}`;
}
