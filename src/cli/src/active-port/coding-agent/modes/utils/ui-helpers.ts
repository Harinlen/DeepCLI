// @ts-nocheck
import { UserMessageComponent } from "../components/user-message";

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
			const statusContainer = this.#ctx?.statusContainer;
			const loadingAnimation = this.#ctx?.loadingAnimation;
			if (loadingAnimation && statusContainer) {
				const children = Array.isArray(statusContainer.children) ? statusContainer.children : undefined;
				if (children && !children.includes(loadingAnimation)) {
					statusContainer.clear?.();
					statusContainer.addChild?.(loadingAnimation);
				}
				this.#ctx.lastStatusText = undefined;
			} else {
				statusContainer?.clear?.();
				statusContainer?.addChild?.(child);
				if (this.#ctx) this.#ctx.lastStatusText = child;
			}
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
		if (!text) return;
		const role = (message as any)?.role;
		const synthetic = role === "developer" ? true : Boolean((message as any)?.synthetic);
		this.#ctx?.chatContainer?.addChild?.(new UserMessageComponent(text, synthetic));
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
