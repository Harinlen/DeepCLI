import { Markdown, applyBackgroundToLine, padding } from "@/tui/index.js";
import { getMarkdownTheme, theme } from "../../modes/theme/theme";

const OSC133_ZONE_START = "\x1b]133;A\x07";
const OSC133_ZONE_END = "\x1b]133;B\x07";
const OSC133_ZONE_FINAL = "\x1b]133;C\x07";

/**
 * Renders a user/developer prompt as a highlighted message block.
 */
export class UserMessageComponent {
	#markdown: Markdown;
	#synthetic: boolean;

	constructor(text: string, synthetic = false) {
		this.#synthetic = synthetic;
		const color = synthetic
			? (value: string) => theme.fg("dim", value)
			: (value: string) => theme.fg("userMessageText", value);

		this.#markdown = new Markdown(text, 0, 0, getMarkdownTheme(), { color });
	}

	invalidate(): void {
		this.#markdown.invalidate();
	}

	render(width: number): string[] {
		const bgColor = (value: string) => theme.bg("userMessageBg", value);
		const marker = theme.fg(this.#synthetic ? "dim" : "muted", ">");
		const markerCellWidth = 3;
		const contentWidth = Math.max(1, width - markerCellWidth - 1);
		const contentLines = this.#markdown.render(contentWidth);

		const lines = [
			"",
			applyBackgroundToLine("", width, bgColor),
			...contentLines.map((line, index) => {
				const prefix = index === 0 ? `${padding(1)}${marker}${padding(1)}` : padding(markerCellWidth);
				return applyBackgroundToLine(prefix + line, width, bgColor);
			}),
			applyBackgroundToLine("", width, bgColor),
		];

		if (lines.length === 0) {
			return lines;
		}

		lines[0] = OSC133_ZONE_START + lines[0];
		lines[lines.length - 1] = lines[lines.length - 1] + OSC133_ZONE_END + OSC133_ZONE_FINAL;
		return lines;
	}
}
