import { BracketedPasteHandler, Container, CURSOR_MARKER, extractPrintableText, matchesKey, replaceTabs, Spacer, Text, type Focusable, type TUI } from "@/tui/index.js";
import { DEEPSEEK_API_KEYS_URL } from "@/startup/oobe.js";
import { theme } from "../theme/theme";
import { matchesAppInterrupt } from "../utils/keybinding-matchers";
import { DynamicBorder } from "./dynamic-border";

const LABEL_WIDTH = 16;

export class DeepSeekOobeSetupComponent extends Container implements Focusable {
	#focused = false;
	#apiKey: string;
	#error: string | undefined;
	#saving = false;
	#pasteHandler = new BracketedPasteHandler();

	constructor(
		private readonly tui: TUI,
		initialApiKey: string,
		private readonly baseUrl: string,
		private readonly onSave: (apiKey: string) => void | Promise<void>,
		private readonly onBack: () => void,
		private readonly onExit?: () => void,
	) {
		super();
		this.#apiKey = initialApiKey;
		this.#renderBody();
	}

	get focused(): boolean {
		return this.#focused;
	}

	set focused(value: boolean) {
		if (this.#focused === value) return;
		this.#focused = value;
		this.#renderBody();
	}

	setError(message: string): void {
		this.#error = message;
		this.#saving = false;
		this.#renderBody();
	}

	handleInput(keyData: string): void {
		const paste = this.#pasteHandler.process(keyData);
		if (paste.handled) {
			if (paste.pasteContent !== undefined) {
				this.#insertText(replaceTabs(paste.pasteContent.replace(/\r\n/g, "").replace(/\r/g, "").replace(/\n/g, "")));
				if (paste.remaining.length > 0) this.handleInput(paste.remaining);
			}
			return;
		}
		if (this.#saving) return;
		if (matchesKey(keyData, "ctrl+c")) {
			(this.onExit ?? this.onBack)();
			return;
		}
		if (matchesAppInterrupt(keyData)) {
			this.onBack();
			return;
		}
		if (matchesKey(keyData, "enter") || matchesKey(keyData, "return") || keyData === "\n") {
			void this.#save();
			return;
		}
		if (matchesKey(keyData, "backspace")) {
			this.#apiKey = this.#apiKey.slice(0, -1);
			this.#error = undefined;
			this.#renderBody();
			return;
		}
		const printable = extractPrintableText(keyData);
		if (printable) this.#insertText(printable);
	}

	#insertText(text: string): void {
		if (!text) return;
		this.#apiKey += text;
		this.#error = undefined;
		this.#renderBody();
	}

	async #save(): Promise<void> {
		const key = this.#apiKey.trim();
		if (!key) {
			this.setError("API key must not be empty");
			return;
		}
		try {
			this.#saving = true;
			this.#error = undefined;
			this.#renderBody();
			await this.onSave(key);
		} catch (error) {
			this.setError(error instanceof Error ? error.message : String(error));
		}
	}

	#renderBody(): void {
		this.clear();
		this.addChild(new DynamicBorder());
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("accent", "  Get a DeepSeek API key"), 0, 0));
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("muted", "  Create an API key in DeepSeek Platform, then paste it below."), 0, 0));
		this.addChild(new Text(`  ${theme.fg("text", DEEPSEEK_API_KEYS_URL)}`, 0, 0));
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("accent", "  Provider Settings"), 0, 0));
		this.#addStaticField("Name:", "deepseek");
		this.#addStaticField("Type:", "deepseek");
		this.#addValueField("API key:", this.#formatApiKey());
		this.#addStaticField("Base URL:", this.baseUrl);
		this.addChild(new Spacer(1));
		this.addChild(new Text(theme.fg("accent", "  Models to add"), 0, 0));
		this.addChild(new Text(`   ${theme.fg("success", "[x]")} ${theme.fg("text", "DeepSeek V4 Pro")} ${theme.fg("muted", "<1M> · default, memory")}`, 0, 0));
		this.addChild(new Text(`   ${theme.fg("success", "[x]")} ${theme.fg("text", "DeepSeek V4 Flash")} ${theme.fg("muted", "<1M> · compact")}`, 0, 0));
		if (this.#error) {
			this.addChild(new Spacer(1));
			this.addChild(new Text(theme.fg("error", `  ${this.#error}`), 0, 0));
		}
		this.addChild(new Spacer(1));
		const hint = this.#saving ? "  Saving..." : "  <Enter> save  <Esc> back";
		this.addChild(new Text(theme.fg("dim", hint), 0, 0));
		this.addChild(new Spacer(1));
		this.addChild(new DynamicBorder());
		this.tui.requestRender();
	}

	#addStaticField(label: string, value: string): void {
		this.addChild(new Text(`   ${theme.fg("muted", label.padEnd(LABEL_WIDTH))}${theme.fg("text", value)}`, 0, 0));
	}

	#addValueField(label: string, value: string): void {
		this.addChild(new Text(`-> ${theme.fg("accent", label.padEnd(LABEL_WIDTH))}${value}`, 0, 0));
	}

	#formatApiKey(): string {
		const text = this.#apiKey.trim();
		if (text) return `${theme.fg("text", text)}${this.#focused ? CURSOR_MARKER : ""}`;
		return `${theme.fg("muted", "<empty>")}${this.#focused ? CURSOR_MARKER : ""}`;
	}
}
