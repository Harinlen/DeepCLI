// -nocheck
export type EditMode = "replace" | "patch" | "chunk" | "hashline";

export interface PerFileDiffPreview {
	filePath: string;
	diff?: string;
	status?: string;
}

export const EDIT_MODE_STRATEGIES: Record<string, { label: string }> = {
	replace: { label: "replace" },
	patch: { label: "patch" },
	chunk: { label: "chunk" },
	hashline: { label: "hashline" },
};
