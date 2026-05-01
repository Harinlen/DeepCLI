// @ts-nocheck
export class UiHelpers {
	#ctx: any;
	constructor(ctx?: unknown) {
		this.#ctx = ctx;
	}
	showStatus(message: string): void {
		this.showMessage(message);
	}
	showInfo(..._args: unknown[]): void {}
	showError(message?: unknown): void {
		this.showMessage(`Error: ${String(message ?? "")}`);
	}
	showWarning(message?: unknown): void {
		this.showMessage(String(message ?? ""));
	}
	showMessage(message?: unknown): void {
		const text = String(message ?? "");
		if (!text) return;
		try {
			const child = { render: () => [text], invalidate: () => {} };
			this.#ctx?.statusContainer?.clear?.();
			this.#ctx?.statusContainer?.addChild?.(child);
			this.#ctx?.ui?.requestRender?.();
		} catch {}
	}
	showNewVersionNotification(..._args: unknown[]): void {}
	clearEditor(): void {
		this.#ctx?.editor?.setText?.("");
		this.#ctx?.ui?.requestRender?.();
	}
	updatePendingMessagesDisplay(): void {
		this.#ctx?.ui?.requestRender?.();
	}
	queueCompactionMessage(..._args: unknown[]): void {}
	flushCompactionQueue(): { steering: string[]; followUp: string[] } {
		return { steering: [], followUp: [] };
	}
	flushPendingBashComponents(): void {}
	isKnownSlashCommand(text: string): boolean {
		return typeof text === "string" && text.startsWith("/");
	}
	addMessageToChat(message: unknown): void {
		const text = this.getUserMessageText(message);
		this.#ctx?.chatContainer?.addChild?.({ render: () => [text ? `> ${text}` : ""], invalidate: () => {} });
	}
	renderSessionContext(..._args: unknown[]): void {}
	renderInitialMessages(): void {}
	getUserMessageText(message: any): string {
		return String(message?.content?.[0]?.text ?? message?.text ?? "");
	}
	findLastAssistantMessage(): unknown {
		return undefined;
	}
	extractAssistantText(message: any): string {
		return String(message?.content?.find?.((part: any) => part.type === "text")?.text ?? "");
	}
}
