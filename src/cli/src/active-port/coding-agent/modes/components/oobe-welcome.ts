import { Container, matchesKey, Spacer, Text, type Focusable } from "@/tui/index.js";
import { theme } from "../theme/theme";
import { matchesAppInterrupt } from "../utils/keybinding-matchers";
import { DynamicBorder } from "./dynamic-border";

export type OobeWelcomeChoice = "deepseek" | "others" | "skip" | "exit";

export interface OobeWelcomeCopy {
	title?: string;
	lines?: string[];
	skipLabel?: string;
	ctrlCChoice?: OobeWelcomeChoice;
}

export class OobeWelcomeComponent extends Container implements Focusable {
	#focused = false;
	#selectedIndex = 0;

	constructor(
		private readonly onSelect: (choice: OobeWelcomeChoice) => void,
		private readonly copy: OobeWelcomeCopy = {},
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
		if (matchesKey(keyData, "ctrl+c")) {
			this.onSelect(this.copy.ctrlCChoice ?? "skip");
			return;
		}
		if (matchesAppInterrupt(keyData)) {
			this.onSelect("skip");
			return;
		}
		if (matchesKey(keyData, "up")) {
			this.#selectedIndex = Math.max(0, this.#selectedIndex - 1);
			this.#renderBody();
			return;
		}
		if (matchesKey(keyData, "down") || matchesKey(keyData, "tab")) {
			this.#selectedIndex = Math.min(this.#options().length - 1, this.#selectedIndex + 1);
			this.#renderBody();
			return;
		}
		if (matchesKey(keyData, "enter") || matchesKey(keyData, "return") || keyData === "\n") {
			this.onSelect(this.#options()[this.#selectedIndex]?.choice ?? "skip");
		}
	}

	#renderBody(): void {
		this.clear();
		const lines = this.copy.lines ?? [
			"DeepCLI needs an LLM model for chat and agent work.",
			"DeepSeek is the quickest way to get started, and you can change this later with /model.",
		];
		this.addChild(new DynamicBorder());
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("accent", `  ${this.copy.title ?? "Welcome to DeepCLI"}`), 0, 0));
		this.addChild(new Spacer(1));
		for (const line of lines) {
			this.addChild(new Text(theme.fg("muted", `  ${line}`), 0, 0));
		}
		this.addChild(new Spacer(1));
		const options = this.#options();
		for (let i = 0; i < options.length; i++) {
			const option = options[i];
			const selected = i === this.#selectedIndex;
			const prefix = selected ? theme.fg("accent", "-> ") : "   ";
			const label = selected ? theme.fg("accent", option.label) : theme.fg("text", option.label);
			this.addChild(new Text(`${prefix}${label}`, 0, 0));
		}
		this.addChild(new Spacer(1));
		this.addChild(new DynamicBorder());
	}

	#options(): Array<{ choice: OobeWelcomeChoice; label: string }> {
		return [
			{ choice: "deepseek", label: "Set up DeepSeek" },
			{ choice: "others", label: "Set up others" },
			{ choice: "skip", label: this.copy.skipLabel ?? "Skip to main window" },
		];
	}
}
