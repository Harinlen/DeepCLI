import { Container, matchesKey, Spacer, Text, type Focusable } from "@/tui/index.js";
import { theme } from "../theme/theme";
import { matchesAppInterrupt } from "../utils/keybinding-matchers";
import { DynamicBorder } from "./dynamic-border";

export interface SimpleOption {
	label: string;
	description?: string;
}

export class SimpleOptionSelectorComponent extends Container implements Focusable {
	#focused = false;
	#selectedIndex = 0;

	constructor(
		private readonly title: string,
		private readonly options: SimpleOption[],
		private readonly onSelect: (index: number) => void,
		private readonly onCancel: () => void,
	) {
		super();
		this.#renderBody();
	}

	get focused(): boolean {
		return this.#focused;
	}

	set focused(value: boolean) {
		this.#focused = value;
		this.#renderBody();
	}

	handleInput(keyData: string): void {
		if (matchesAppInterrupt(keyData)) {
			this.onCancel();
			return;
		}
		if (matchesKey(keyData, "up")) {
			this.#selectedIndex = Math.max(0, this.#selectedIndex - 1);
			this.#renderBody();
			return;
		}
		if (matchesKey(keyData, "down") || matchesKey(keyData, "tab")) {
			this.#selectedIndex = Math.min(this.options.length - 1, this.#selectedIndex + 1);
			this.#renderBody();
			return;
		}
		if (matchesKey(keyData, "enter") || matchesKey(keyData, "return") || keyData === "\n") {
			this.onSelect(this.#selectedIndex);
		}
	}

	#renderBody(): void {
		this.clear();
		this.addChild(new DynamicBorder());
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("accent", `  ${this.title}`), 0, 0));
		this.addChild(new Spacer(1));
		for (let i = 0; i < this.options.length; i++) {
			const option = this.options[i];
			const selected = i === this.#selectedIndex;
			const prefix = selected ? theme.fg("accent", "-> ") : "   ";
			const label = selected ? theme.fg("accent", option.label) : theme.fg("text", option.label);
			const description = option.description ? `  ${theme.fg("muted", option.description)}` : "";
			this.addChild(new Text(`${prefix}${label}${description}`, 0, 0));
		}
		this.addChild(new Spacer(1));
		this.addChild(new DynamicBorder());
	}
}
