// @ts-nocheck
import {
	type Component,
	Container,
	extractPrintableText,
	Input,
	matchesKey,
	Spacer,
	Text,
	TruncatedText,
	truncateToWidth,
} from "@/tui/index.js";
import { theme } from "../../modes/theme/theme";
import { matchesAppInterrupt } from "../../modes/utils/keybinding-matchers";
import { DynamicBorder } from "./dynamic-border";

type FilterMode = "default" | "no-tools" | "user-only" | "labeled-only" | "all";

interface FlatNode {
	node: any;
	depth: number;
}

class TreeList implements Component {
	#flatNodes: FlatNode[] = [];
	#filteredNodes: FlatNode[] = [];
	#selectedIndex = 0;
	#filterMode: FilterMode;
	#searchQuery = "";

	onSelect?: (entryId: string) => void;
	onCancel?: () => void;
	onLabelEdit?: (entryId: string, currentLabel: string | undefined) => void;

	constructor(
		tree: any[],
		private readonly currentLeafId: string | null,
		private readonly maxVisibleLines: number,
		initialFilterMode: FilterMode = "default",
	) {
		this.#filterMode = initialFilterMode;
		this.#flatNodes = this.#flatten(tree);
		this.#applyFilter();
		const currentIndex = this.#filteredNodes.findIndex(item => item.node.entry.id === currentLeafId);
		this.#selectedIndex = currentIndex >= 0 ? currentIndex : 0;
	}

	#flatten(nodes: any[], depth = 0): FlatNode[] {
		const result: FlatNode[] = [];
		for (const node of nodes) {
			result.push({ node, depth });
			result.push(...this.#flatten(node.children ?? [], depth + 1));
		}
		return result;
	}

	#applyFilter(): void {
		const query = this.#searchQuery.toLowerCase();
		this.#filteredNodes = this.#flatNodes.filter(item => {
			const entry = item.node.entry ?? {};
			if (this.#filterMode === "user-only" && !(entry.type === "message" && entry.message?.role === "user")) {
				return false;
			}
			if (this.#filterMode === "no-tools" && entry.type === "message" && entry.message?.role === "toolResult") {
				return false;
			}
			if (this.#filterMode === "labeled-only" && !item.node.label) {
				return false;
			}
			if (this.#filterMode === "default") {
				const hidden = ["label", "custom", "model_change", "thinking_level_change"];
				if (hidden.includes(entry.type)) return false;
			}
			if (!query) return true;
			return this.#nodeText(item.node).toLowerCase().includes(query);
		});
		this.#selectedIndex = Math.min(this.#selectedIndex, Math.max(0, this.#filteredNodes.length - 1));
	}

	#nodeText(node: any): string {
		const entry = node.entry ?? {};
		const parts = [node.label ?? "", entry.type ?? "", entry.customType ?? "", entry.summary ?? ""];
		const message = entry.message;
		if (message) {
			parts.push(message.role ?? "", this.#contentText(message.content), message.command ?? "");
		}
		if (typeof entry.content === "string") {
			parts.push(entry.content);
		} else if (Array.isArray(entry.content)) {
			parts.push(this.#contentText(entry.content));
		}
		return parts.filter(Boolean).join(" ");
	}

	#contentText(content: unknown): string {
		if (typeof content === "string") return content;
		if (!Array.isArray(content)) return "";
		return content
			.map(block => {
				if (!block || typeof block !== "object") return "";
				if ("text" in block) return String(block.text ?? "");
				if ("thinking" in block) return String(block.thinking ?? "");
				return "";
			})
			.filter(Boolean)
			.join(" ");
	}

	#filterLabel(): string {
		if (this.#filterMode === "default") return "";
		if (this.#filterMode === "no-tools") return " [no-tools]";
		if (this.#filterMode === "user-only") return " [user]";
		if (this.#filterMode === "labeled-only") return " [labeled]";
		return " [all]";
	}

	getSearchQuery(): string {
		return this.#searchQuery;
	}

	getSelectedNode(): any | undefined {
		return this.#filteredNodes[this.#selectedIndex]?.node;
	}

	updateNodeLabel(entryId: string, label: string | undefined): void {
		const item = this.#flatNodes.find(flat => flat.node.entry?.id === entryId);
		if (item) item.node.label = label;
		this.#applyFilter();
	}

	render(width: number): string[] {
		if (this.#filteredNodes.length === 0) {
			return [
				truncateToWidth(theme.fg("muted", "  No entries found"), width),
				truncateToWidth(theme.fg("muted", `  (0/0)${this.#filterLabel()}`), width),
			];
		}

		const start = Math.max(
			0,
			Math.min(this.#selectedIndex - Math.floor(this.maxVisibleLines / 2), this.#filteredNodes.length - this.maxVisibleLines),
		);
		const end = Math.min(start + this.maxVisibleLines, this.#filteredNodes.length);
		const lines: string[] = [];
		for (let index = start; index < end; index++) {
			const item = this.#filteredNodes[index];
			const selected = index === this.#selectedIndex;
			const marker = selected ? theme.fg("accent", "› ") : "  ";
			const indent = "  ".repeat(item.depth);
			const active = item.node.entry?.id === this.currentLeafId ? theme.fg("accent", `${theme.md.bullet} `) : "";
			const label = item.node.label ? theme.fg("warning", `[${item.node.label}] `) : "";
			const text = this.#nodeDisplay(item.node);
			const line = marker + theme.fg("dim", indent) + active + label + (selected ? theme.bold(text) : text);
			lines.push(truncateToWidth(selected ? theme.bg("selectedBg", line) : line, width));
		}
		lines.push(truncateToWidth(theme.fg("muted", `  (${this.#selectedIndex + 1}/${this.#filteredNodes.length})${this.#filterLabel()}`), width));
		return lines;
	}

	#nodeDisplay(node: any): string {
		const entry = node.entry ?? {};
		if (entry.type === "message") {
			const role = entry.message?.role ?? "message";
			const text = this.#contentText(entry.message?.content).replace(/\s+/g, " ").trim();
			return `${role}: ${text || "(no content)"}`;
		}
		if (entry.type === "custom_message") return `[${entry.customType}]: ${this.#nodeText(node)}`;
		if (entry.type === "compaction") return "[compaction]";
		if (entry.type === "branch_summary") return `[branch summary]: ${entry.summary ?? ""}`;
		if (entry.type === "label") return `[label: ${entry.label ?? "(cleared)"}]`;
		return `[${entry.type ?? "entry"}]`;
	}

	handleInput(keyData: string): void {
		if (matchesKey(keyData, "up")) {
			this.#selectedIndex = this.#selectedIndex === 0 ? Math.max(0, this.#filteredNodes.length - 1) : this.#selectedIndex - 1;
		} else if (matchesKey(keyData, "down")) {
			this.#selectedIndex = this.#selectedIndex === this.#filteredNodes.length - 1 ? 0 : this.#selectedIndex + 1;
		} else if (matchesKey(keyData, "left")) {
			this.#selectedIndex = Math.max(0, this.#selectedIndex - this.maxVisibleLines);
		} else if (matchesKey(keyData, "right")) {
			this.#selectedIndex = Math.min(this.#filteredNodes.length - 1, this.#selectedIndex + this.maxVisibleLines);
		} else if (matchesKey(keyData, "enter") || matchesKey(keyData, "return") || keyData === "\n") {
			const selected = this.#filteredNodes[this.#selectedIndex];
			if (selected) this.onSelect?.(selected.node.entry.id);
		} else if (matchesAppInterrupt(keyData)) {
			if (this.#searchQuery) {
				this.#searchQuery = "";
				this.#applyFilter();
			} else {
				this.onCancel?.();
			}
		} else if (matchesKey(keyData, "ctrl+c")) {
			this.onCancel?.();
		} else if (matchesKey(keyData, "shift+ctrl+o") || matchesKey(keyData, "ctrl+shift+o")) {
			this.#cycleFilter(-1);
		} else if (matchesKey(keyData, "ctrl+o")) {
			this.#cycleFilter(1);
		} else if (matchesKey(keyData, "alt+d")) {
			this.#setFilter("default");
		} else if (matchesKey(keyData, "alt+t")) {
			this.#setFilter("no-tools");
		} else if (matchesKey(keyData, "alt+u")) {
			this.#setFilter("user-only");
		} else if (matchesKey(keyData, "alt+l")) {
			this.#setFilter("labeled-only");
		} else if (matchesKey(keyData, "alt+a")) {
			this.#setFilter("all");
		} else if (matchesKey(keyData, "backspace")) {
			this.#searchQuery = this.#searchQuery.slice(0, -1);
			this.#applyFilter();
		} else if (matchesKey(keyData, "shift+l") && !this.#searchQuery) {
			const selected = this.#filteredNodes[this.#selectedIndex];
			if (selected) this.onLabelEdit?.(selected.node.entry.id, selected.node.label);
		} else {
			const text = extractPrintableText(keyData);
			if (text) {
				this.#searchQuery += text;
				this.#applyFilter();
			}
		}
	}

	#cycleFilter(direction: 1 | -1): void {
		const modes: FilterMode[] = ["default", "no-tools", "user-only", "labeled-only", "all"];
		const current = modes.indexOf(this.#filterMode);
		this.#setFilter(modes[(current + direction + modes.length) % modes.length]);
	}

	#setFilter(mode: FilterMode): void {
		this.#filterMode = mode;
		this.#applyFilter();
	}
}

class SearchLine implements Component {
	constructor(private readonly treeList: TreeList) {}
	invalidate(): void {}
	render(width: number): string[] {
		const query = this.treeList.getSearchQuery();
		return [truncateToWidth(`  ${theme.fg("muted", "Search:")} ${query ? theme.fg("accent", query) : ""}`, width)];
	}
	handleInput(): void {}
}

class LabelInput implements Component {
	#input = new Input();
	onSubmit?: (entryId: string, label: string | undefined) => void;
	onCancel?: () => void;

	constructor(
		private readonly entryId: string,
		currentLabel: string | undefined,
	) {
		if (currentLabel) this.#input.setValue(currentLabel);
	}

	invalidate(): void {}
	render(width: number): string[] {
		return [
			truncateToWidth(`  ${theme.fg("muted", "Label (empty to remove):")}`, width),
			...this.#input.render(Math.max(10, width - 2)).map(line => truncateToWidth(`  ${line}`, width)),
			truncateToWidth(`  ${theme.fg("dim", "enter: save  esc: cancel")}`, width),
		];
	}
	handleInput(keyData: string): void {
		if (matchesKey(keyData, "enter") || matchesKey(keyData, "return") || keyData === "\n") {
			const value = this.#input.getValue().trim();
			this.onSubmit?.(this.entryId, value || undefined);
		} else if (matchesAppInterrupt(keyData)) {
			this.onCancel?.();
		} else {
			this.#input.handleInput(keyData);
		}
	}
}

export class TreeSelectorComponent extends Container {
	#treeList: TreeList;
	#treeContainer = new Container();
	#labelInputContainer = new Container();
	#labelInput: LabelInput | null = null;

	constructor(
		tree: any[],
		currentLeafId: string | null,
		terminalHeight: number,
		onSelect: (entryId: string) => void,
		onCancel: () => void,
		private readonly onLabelChangeCallback?: (entryId: string, label: string | undefined) => void,
		initialFilterMode: FilterMode = "default",
	) {
		super();
		this.#treeList = new TreeList(tree, currentLeafId, Math.max(5, Math.floor(terminalHeight / 2)), initialFilterMode);
		this.#treeList.onSelect = onSelect;
		this.#treeList.onCancel = onCancel;
		this.#treeList.onLabelEdit = (entryId, currentLabel) => this.#showLabelInput(entryId, currentLabel);

		this.#treeContainer.addChild(this.#treeList);
		this.addChild(new Spacer(1));
		this.addChild(new DynamicBorder());
		this.addChild(new Text(theme.bold("  Session Tree"), 1, 0));
		this.addChild(
			new TruncatedText(
				theme.fg(
					"muted",
					"Up/Down: move. Left/Right: page. Shift+L: label. Ctrl+O/Shift+Ctrl+O: filter. Alt+D/T/U/L/A: filter. Type to search",
				),
				0,
				0,
			),
		);
		this.addChild(new SearchLine(this.#treeList));
		this.addChild(new DynamicBorder());
		this.addChild(new Spacer(1));
		this.addChild(this.#treeContainer);
		this.addChild(this.#labelInputContainer);
		this.addChild(new Spacer(1));
		this.addChild(new DynamicBorder());
	}

	#showLabelInput(entryId: string, currentLabel: string | undefined): void {
		this.#labelInput = new LabelInput(entryId, currentLabel);
		this.#labelInput.onSubmit = (id, label) => {
			this.#treeList.updateNodeLabel(id, label);
			this.onLabelChangeCallback?.(id, label);
			this.#hideLabelInput();
		};
		this.#labelInput.onCancel = () => this.#hideLabelInput();
		this.#treeContainer.clear();
		this.#labelInputContainer.clear();
		this.#labelInputContainer.addChild(this.#labelInput);
	}

	#hideLabelInput(): void {
		this.#labelInput = null;
		this.#labelInputContainer.clear();
		this.#treeContainer.clear();
		this.#treeContainer.addChild(this.#treeList);
	}

	handleInput(keyData: string): void {
		if (this.#labelInput) {
			this.#labelInput.handleInput(keyData);
		} else {
			this.#treeList.handleInput(keyData);
		}
	}

	getTreeList(): TreeList {
		return this.#treeList;
	}
}
