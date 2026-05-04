// -nocheck
export type PermissionMode = "default" | "accept_edits" | "plan" | "auto" | "dont_ask" | "bypass";

export function permissionModeDisplay(mode: PermissionMode | string | undefined): { label: string; title: string; icon: string; color: string; description: string } {
	switch (mode) {
		case "accept_edits":
			return {
				label: "Edits",
				title: "Edit automatically",
				icon: "✎",
				color: "text",
				description: "DeepCLI will edit your selected text or the whole file.",
			};
		case "plan":
			return {
				label: "Plan",
				title: "Plan mode",
				icon: "▤",
				color: "#0078d4",
				description: "DeepCLI will explore the code and present a plan before editing.",
			};
		case "auto":
			return {
				label: "Auto",
				title: "Auto mode",
				icon: "⚡",
				color: "#f85149",
				description: "DeepCLI will automatically choose the best permission mode for each task.",
			};
		case "dont_ask":
			return {
				label: "No ask",
				title: "Don't ask",
				icon: "⏭",
				color: "muted",
				description: "DeepCLI will not ask for approval and will only run pre-approved tools.",
			};
		case "bypass":
			return {
				label: "Bypass",
				title: "Bypass permissions",
				icon: "⚠",
				color: "#f85149",
				description: "DeepCLI will not ask for approval before running potentially dangerous commands.",
			};
		case "default":
		default:
			return {
				label: "Ask",
				title: "Ask before edits",
				icon: "✋",
				color: "accent",
				description: "DeepCLI will ask for approval before making each edit.",
			};
	}
}
