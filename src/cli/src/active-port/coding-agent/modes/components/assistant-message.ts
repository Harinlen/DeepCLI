// @ts-nocheck
import type { AssistantMessage, ImageContent, Usage } from "@/compat/ai.js";
import { Container, Image, ImageProtocol, Markdown, Spacer, TERMINAL, Text } from "@/tui/index.js";
import { formatDuration, formatNumber } from "@/compat/utils.js";
import { settings } from "../../config/settings";
import { getMarkdownTheme, theme } from "../../modes/theme/theme";
import { resolveImageOptions } from "../../tools/render-utils";

/**
 * Component that renders a complete assistant message
 */
export class AssistantMessageComponent extends Container {
	#contentContainer: Container;
	#lastMessage?: AssistantMessage;
	#toolImagesByCallId = new Map<string, ImageContent[]>();
	#usageInfo?: Usage;
	#durationMs?: number;

	constructor(
		message?: AssistantMessage,
		private hideThinkingBlock = true,
	) {
		super();

		// Container for text/thinking content
		this.#contentContainer = new Container();
		this.addChild(this.#contentContainer);

		if (message) {
			this.updateContent(message);
		}
	}

	override invalidate(): void {
		super.invalidate();
		if (this.#lastMessage) {
			this.updateContent(this.#lastMessage);
		}
	}

	setHideThinkingBlock(hide: boolean): void {
		this.hideThinkingBlock = hide;
	}

	setToolResultImages(toolCallId: string, images: ImageContent[]): void {
		if (!toolCallId) return;
		const validImages = images.filter(img => img.type === "image" && img.data && img.mimeType);
		if (validImages.length === 0) {
			this.#toolImagesByCallId.delete(toolCallId);
		} else {
			this.#toolImagesByCallId.set(toolCallId, validImages);
		}
		if (this.#lastMessage) {
			this.updateContent(this.#lastMessage);
		}
	}

	setUsageInfo(usage: Usage | undefined, durationMs?: number): void {
		this.#usageInfo = usage;
		this.#durationMs = durationMs;
		if (this.#lastMessage) {
			this.updateContent(this.#lastMessage);
		}
	}

	#renderToolImages(): void {
		const images = Array.from(this.#toolImagesByCallId.values()).flat();
		if (images.length === 0) return;

		this.#contentContainer.addChild(new Spacer(1));
		for (const image of images) {
			if (
				TERMINAL.imageProtocol &&
				(TERMINAL.imageProtocol !== ImageProtocol.Kitty || image.mimeType === "image/png")
			) {
				this.#contentContainer.addChild(
					new Image(
						image.data,
						image.mimeType,
						{ fallbackColor: (text: string) => theme.fg("toolOutput", text) },
						resolveImageOptions(),
					),
				);
				continue;
			}
			this.#contentContainer.addChild(new Text(theme.fg("toolOutput", `[Image: ${image.mimeType}]`), 1, 0));
		}
	}

	updateContent(message: AssistantMessage): void {
		this.#lastMessage = message;

		// Clear content container
		this.#contentContainer.clear();

		const hasVisibleContent = message.content.some(
			c => (c.type === "text" && c.text.trim()) || (c.type === "thinking" && c.thinking.trim()),
		);

		if (hasVisibleContent) {
			this.#contentContainer.addChild(new Spacer(1));
		}

		// Render content in order
		for (let i = 0; i < message.content.length; i++) {
			const content = message.content[i];
			if (content.type === "text" && content.text.trim()) {
				// Assistant text messages with no background - trim the text
				// Set paddingY=0 to avoid extra spacing before tool executions
				this.#contentContainer.addChild(new Markdown(content.text.trim(), 1, 0, getMarkdownTheme()));
			} else if (content.type === "thinking" && content.thinking.trim()) {
				// Add spacing only when another visible assistant content block follows.
				// This avoids a superfluous blank line before separately-rendered tool execution blocks.
				const hasVisibleContentAfter = message.content
					.slice(i + 1)
					.some(c => (c.type === "text" && c.text.trim()) || (c.type === "thinking" && c.thinking.trim()));

				if (this.hideThinkingBlock) {
					// Show static "Thinking..." label when hidden
					this.#contentContainer.addChild(new Text(theme.italic(theme.fg("thinkingText", "Thinking...")), 1, 0));
					if (hasVisibleContentAfter) {
						this.#contentContainer.addChild(new Spacer(1));
					}
				} else {
					// Thinking traces in thinkingText color, italic
					this.#contentContainer.addChild(
						new Markdown(content.thinking.trim(), 1, 0, getMarkdownTheme(), {
							color: (text: string) => theme.fg("thinkingText", text),
							codeColor: (text: string) => theme.fg("thinkingText", text),
							italic: true,
						}),
					);
					if (hasVisibleContentAfter) {
						this.#contentContainer.addChild(new Spacer(1));
					}
				}
			}
		}

		this.#renderToolImages();
		// Check if aborted - show after partial content
		// But only if there are no tool calls (tool execution components will show the error)
		const hasToolCalls = message.content.some(c => c.type === "toolCall");
		if (!hasToolCalls) {
			if (message.stopReason === "aborted") {
				const abortMessage =
					message.errorMessage && message.errorMessage !== "Request was aborted"
						? message.errorMessage
						: "Operation aborted";
				if (hasVisibleContent) {
					this.#contentContainer.addChild(new Spacer(1));
				} else {
					this.#contentContainer.addChild(new Spacer(1));
				}
				this.#contentContainer.addChild(new Text(theme.fg("error", abortMessage), 1, 0));
			} else if (message.stopReason === "error" && (message.errorMessage || !hasVisibleContent)) {
				const errorMsg = message.errorMessage || "Unknown error";
				this.#contentContainer.addChild(new Spacer(1));
				this.#contentContainer.addChild(new Text(theme.fg("error", `Error: ${errorMsg}`), 1, 0));
			}
		}

		// Token usage metadata
		if (settings.get("display.showTokenUsage") && this.#usageInfo) {
			const usage = this.#usageInfo;
			const input = usage.input ?? 0;
			const output = usage.output ?? 0;
			const cacheRead = usage.cacheRead ?? 0;
			const cacheWrite = usage.cacheWrite ?? 0;
			const totalInput = input + cacheWrite;
			const hasTokenUsage = totalInput > 0 || output > 0 || cacheRead > 0;
			const hasDuration = Boolean(this.#durationMs && this.#durationMs > 0);
			if (!hasTokenUsage && !hasDuration) return;

			const parts: string[] = [];
			parts.push(`${theme.icon.input} ${formatNumber(totalInput)}`);
			parts.push(`${theme.icon.output} ${formatNumber(output)}`);
			if (cacheRead > 0) {
				parts.push(`cache: ${formatNumber(cacheRead)}`);
			}
			if (this.#durationMs && this.#durationMs > 0) {
				parts.push(`${theme.icon.time} ${formatDuration(this.#durationMs)}`);
			}
			this.#contentContainer.addChild(new Spacer(1));
			this.#contentContainer.addChild(new Text(theme.fg("dim", parts.join("  ")), 1, 0));
		}
	}
}
