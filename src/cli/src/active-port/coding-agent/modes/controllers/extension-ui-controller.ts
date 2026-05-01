// -nocheck
import { Container, Text, type Component, type OverlayHandle, type TUI } from "@/tui/index.js";
import { KeybindingsManager } from "../../config/keybindings";
import type { ExtensionUIDialogOptions } from "../../extensibility/extensions";
import { HookEditorComponent } from "../../modes/components/hook-editor";
import { HookInputComponent } from "../../modes/components/hook-input";
import { HookSelectorComponent } from "../../modes/components/hook-selector";
import { theme, type Theme } from "../../modes/theme/theme";
import type { InteractiveModeContext } from "../../modes/types";

export class ExtensionUiController {
	#extensionTerminalInputUnsubscribers = new Set<() => void>();

	constructor(private ctx: InteractiveModeContext) {}

	dispose(): void {}

	clearExtensionTerminalInputListeners(): void {
		for (const unsubscribe of this.#extensionTerminalInputUnsubscribers) unsubscribe();
		this.#extensionTerminalInputUnsubscribers.clear();
	}

	clearHookWidgets(): void {}
	initializeHookRunner(..._args: unknown[]): void {}
	createBackgroundUiContext(): Record<string, unknown> { return {}; }

	async showHookConfirm(title: string, message: string): Promise<boolean> {
		const result = await this.showHookSelector(`${title}\n${message}`, ["Yes", "No"]);
		return result === "Yes";
	}

	initHooksAndCustomTools(): Promise<void> { return Promise.resolve(); }
	emitCustomToolSessionEvent(..._args: unknown[]): Promise<void> { return Promise.resolve(); }
	setHookWidget(..._args: unknown[]): void {}
	setHookStatus(..._args: unknown[]): void {}

	showHookSelector(
		title: string,
		options: string[],
		dialogOptions?: ExtensionUIDialogOptions,
	): Promise<string | undefined> {
		const { promise, finish, attachAbort } = this.#createHookDialogState(
			() => this.hideHookSelector(),
			dialogOptions?.signal,
		);
		const maxVisible = Math.max(4, Math.min(15, this.ctx.ui.terminal.rows - 12));
		this.ctx.hookSelector = new HookSelectorComponent(
			title,
			options,
			option => {
				this.hideHookSelector();
				finish(option);
			},
			() => {
				this.hideHookSelector();
				finish(undefined);
			},
			{
				onLeft: dialogOptions?.onLeft
					? () => {
							this.hideHookSelector();
							dialogOptions.onLeft?.();
							finish(undefined);
						}
					: undefined,
				onRight: dialogOptions?.onRight
					? () => {
							this.hideHookSelector();
							dialogOptions.onRight?.();
							finish(undefined);
						}
					: undefined,
				onExternalEditor: dialogOptions?.onExternalEditor,
				helpText: dialogOptions?.helpText,
				initialIndex: dialogOptions?.initialIndex,
				timeout: dialogOptions?.timeout,
				onTimeout: dialogOptions?.onTimeout,
				tui: this.ctx.ui,
				outline: dialogOptions?.outline,
				maxVisible,
			},
		);
		this.ctx.editorContainer.clear();
		this.ctx.editorContainer.addChild(this.ctx.hookSelector);
		this.ctx.ui.setFocus(this.ctx.hookSelector);
		this.ctx.ui.requestRender();
		attachAbort();
		return promise;
	}

	hideHookSelector(): void {
		this.ctx.hookSelector?.dispose();
		this.ctx.editorContainer.clear();
		this.ctx.editorContainer.addChild(this.ctx.editor);
		this.ctx.hookSelector = undefined;
		this.ctx.ui.setFocus(this.ctx.editor);
		this.ctx.ui.requestRender();
	}

	showHookInput(
		title: string,
		placeholder?: string,
		dialogOptions?: ExtensionUIDialogOptions,
	): Promise<string | undefined> {
		const { promise, finish, attachAbort } = this.#createHookDialogState(
			() => this.hideHookInput(),
			dialogOptions?.signal,
		);
		this.ctx.hookInput = new HookInputComponent(
			title,
			placeholder,
			value => {
				this.hideHookInput();
				finish(value);
			},
			() => {
				this.hideHookInput();
				finish(undefined);
			},
			{
				timeout: dialogOptions?.timeout,
				onTimeout: dialogOptions?.onTimeout,
				tui: this.ctx.ui,
			},
		);
		this.ctx.editorContainer.clear();
		this.ctx.editorContainer.addChild(this.ctx.hookInput);
		this.ctx.ui.setFocus(this.ctx.hookInput);
		this.ctx.ui.requestRender();
		attachAbort();
		return promise;
	}

	hideHookInput(): void {
		this.ctx.hookInput?.dispose();
		this.ctx.editorContainer.clear();
		this.ctx.editorContainer.addChild(this.ctx.editor);
		this.ctx.hookInput = undefined;
		this.ctx.ui.setFocus(this.ctx.editor);
		this.ctx.ui.requestRender();
	}

	showHookEditor(
		title: string,
		prefill?: string,
		dialogOptions?: ExtensionUIDialogOptions,
		editorOptions?: { promptStyle?: boolean },
	): Promise<string | undefined> {
		const { promise, finish, attachAbort } = this.#createHookDialogState(
			() => this.hideHookEditor(),
			dialogOptions?.signal,
		);
		this.ctx.hookEditor = new HookEditorComponent(
			this.ctx.ui,
			title,
			prefill,
			value => {
				this.hideHookEditor();
				finish(value);
			},
			() => {
				this.hideHookEditor();
				finish(undefined);
			},
			editorOptions,
		);
		this.ctx.editorContainer.clear();
		this.ctx.editorContainer.addChild(this.ctx.hookEditor);
		this.ctx.ui.setFocus(this.ctx.hookEditor);
		this.ctx.ui.requestRender();
		attachAbort();
		return promise;
	}

	hideHookEditor(): void {
		this.ctx.editorContainer.clear();
		this.ctx.editorContainer.addChild(this.ctx.editor);
		this.ctx.hookEditor = undefined;
		this.ctx.ui.setFocus(this.ctx.editor);
		this.ctx.ui.requestRender();
	}

	showHookNotify(message: string, type?: "info" | "warning" | "error"): void {
		if (type === "error") this.ctx.showError(message);
		else if (type === "warning") this.ctx.showWarning(message);
		else this.ctx.showStatus(message);
	}

	async showHookCustom<T>(
		factory: (
			tui: TUI,
			theme: Theme,
			keybindings: KeybindingsManager,
			done: (result: T) => void,
		) => (Component & { dispose?(): void }) | Promise<Component & { dispose?(): void }>,
		options?: { overlay?: boolean },
	): Promise<T> {
		const savedText = this.ctx.editor.getText();
		const { promise, resolve } = Promise.withResolvers<T>();
		let component: (Component & { dispose?(): void }) | undefined;
		let overlayHandle: OverlayHandle | undefined;
		let closed = false;

		const close = (result: T) => {
			if (closed) return;
			closed = true;
			component?.dispose?.();
			overlayHandle?.hide();
			overlayHandle = undefined;
			if (!options?.overlay) {
				this.ctx.editorContainer.clear();
				this.ctx.editorContainer.addChild(this.ctx.editor);
				this.ctx.editor.setText(savedText);
			}
			this.ctx.ui.setFocus(this.ctx.editor);
			this.ctx.ui.requestRender();
			resolve(result);
		};

		Promise.try(() => factory(this.ctx.ui, theme, KeybindingsManager.inMemory(), close)).then(c => {
			if (closed) {
				c.dispose?.();
				return;
			}
			component = c;
			if (options?.overlay) {
				overlayHandle = this.ctx.ui.showOverlay(component, {
					anchor: "bottom-center",
					width: "100%",
					maxHeight: "100%",
					margin: 0,
				});
				return;
			}
			this.ctx.editorContainer.clear();
			this.ctx.editorContainer.addChild(component);
			this.ctx.ui.setFocus(component);
			this.ctx.ui.requestRender();
		});
		return promise;
	}

	showExtensionError(..._args: unknown[]): void {}
	showToolError(toolName: string, error: string): void {
		this.ctx.chatContainer.addChild(new Text(theme.fg("error", `Tool "${toolName}" error: ${error}`), 1, 0));
		this.ctx.ui.requestRender();
	}

	addExtensionTerminalInputListener(handler: (data: string) => unknown): () => void {
		const unsubscribe = this.ctx.ui.addInputListener(handler);
		this.#extensionTerminalInputUnsubscribers.add(unsubscribe);
		return () => {
			unsubscribe();
			this.#extensionTerminalInputUnsubscribers.delete(unsubscribe);
		};
	}

	#createHookDialogState(
		hide: () => void,
		signal: AbortSignal | undefined,
	): {
		promise: Promise<string | undefined>;
		finish: (value: string | undefined) => void;
		attachAbort: () => void;
	} {
		const { promise, resolve } = Promise.withResolvers<string | undefined>();
		let settled = false;
		const onAbort = () => {
			hide();
			if (!settled) {
				settled = true;
				resolve(undefined);
			}
		};
		const finish = (value: string | undefined) => {
			if (settled) return;
			settled = true;
			signal?.removeEventListener("abort", onAbort);
			resolve(value);
		};
		const attachAbort = () => {
			if (!signal) return;
			if (signal.aborted) onAbort();
			else signal.addEventListener("abort", onAbort, { once: true });
		};
		return { promise, finish, attachAbort };
	}
}
