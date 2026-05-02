// -nocheck
import { Container, Spacer, Text, type Component, type OverlayHandle, type TUI } from "@/tui/index.js";
import { KeybindingsManager } from "../../config/keybindings";
import type { ExtensionUIDialogOptions } from "../../extensibility/extensions";
import { HookEditorComponent } from "../../modes/components/hook-editor";
import { HookInputComponent } from "../../modes/components/hook-input";
import { HookSelectorComponent } from "../../modes/components/hook-selector";
import { theme, type Theme } from "../../modes/theme/theme";
import type { InteractiveModeContext } from "../../modes/types";

const MAX_WIDGET_LINES = 10;

export class ExtensionUiController {
	#extensionTerminalInputUnsubscribers = new Set<() => void>();
	#hookWidgetsAbove = new Map<string, Component & { dispose?(): void }>();
	#hookWidgetsBelow = new Map<string, Component & { dispose?(): void }>();

	constructor(private ctx: InteractiveModeContext) {}

	dispose(): void {}

	clearExtensionTerminalInputListeners(): void {
		for (const unsubscribe of this.#extensionTerminalInputUnsubscribers) unsubscribe();
		this.#extensionTerminalInputUnsubscribers.clear();
	}

	clearHookWidgets(): void {
		for (const widget of this.#hookWidgetsAbove.values()) widget.dispose?.();
		for (const widget of this.#hookWidgetsBelow.values()) widget.dispose?.();
		this.#hookWidgetsAbove.clear();
		this.#hookWidgetsBelow.clear();
		this.#rebuildHookWidgets();
	}

	initializeHookRunner(uiContext: Record<string, unknown>, _hasUI: boolean): void {
		const extensionRunner = this.ctx.session.extensionRunner;
		if (!extensionRunner?.initialize) return;
		extensionRunner.initialize(
			this.#createExtensionActions(),
			this.#createExtensionContextActions(),
			this.#createExtensionCommandActions(),
			uiContext,
		);
	}

	createBackgroundUiContext(): Record<string, unknown> {
		return {
			select: async () => undefined,
			confirm: async () => false,
			input: async () => undefined,
			notify: () => {},
			onTerminalInput: () => () => {},
			setStatus: () => {},
			setWorkingMessage: () => {},
			setWidget: () => {},
			setTitle: () => {},
			custom: async () => undefined,
			setEditorText: () => {},
			pasteToEditor: () => {},
			getEditorText: () => "",
			editor: async () => undefined,
			get theme() {
				return theme;
			},
			getAllThemes: async () => [],
			getTheme: async () => undefined,
			setTheme: async () => ({ success: false, error: "Background mode" }),
			setFooter: () => {},
			setHeader: () => {},
			setEditorComponent: () => {},
			getToolsExpanded: () => false,
			setToolsExpanded: () => {},
		};
	}

	async showHookConfirm(title: string, message: string): Promise<boolean> {
		const result = await this.showHookSelector(`${title}\n${message}`, ["Yes", "No"]);
		return result === "Yes";
	}

	async initHooksAndCustomTools(): Promise<void> {
		const uiContext = this.#createInteractiveUiContext();
		this.ctx.setToolUIContext(uiContext, true);
		this.initializeHookRunner(uiContext, true);
		const extensionRunner = this.ctx.session.extensionRunner;
		if (extensionRunner?.onError) {
			extensionRunner.onError((error: { extensionPath?: string; error?: string }) => {
				this.showExtensionError(error.extensionPath ?? "extension", error.error ?? "Unknown error");
			});
		}
		await extensionRunner?.emit?.({ type: "session_start" });
	}

	async emitCustomToolSessionEvent(
		reason: "start" | "switch" | "branch" | "tree" | "shutdown",
		previousSessionFile?: string,
	): Promise<void> {
		const runner = this.ctx.session.extensionRunner;
		const uiContext = runner?.getUIContext?.();
		if (!runner || !uiContext) return;
		for (const registeredTool of runner.getAllRegisteredTools?.() ?? []) {
			if (!registeredTool.definition?.onSession) continue;
			try {
				await registeredTool.definition.onSession(
					{ reason, previousSessionFile },
					{
						ui: uiContext,
						getContextUsage: () => this.ctx.session.getContextUsage?.(),
						compact: (instructionsOrOptions: unknown) => this.#compactSession(instructionsOrOptions),
						hasUI: !this.ctx.isBackgrounded,
						cwd: this.ctx.sessionManager.getCwd(),
						sessionManager: this.ctx.session.sessionManager,
						modelRegistry: this.ctx.session.modelRegistry,
						model: this.ctx.session.model,
						isIdle: () => !this.ctx.session.isStreaming,
						hasPendingMessages: () => this.ctx.session.queuedMessageCount > 0,
						hasQueuedMessages: () => this.ctx.session.queuedMessageCount > 0,
						abort: () => this.ctx.session.abort(),
						shutdown: () => {},
						getSystemPrompt: () => this.ctx.session.systemPrompt,
					},
				);
			} catch (err) {
				this.showToolError(registeredTool.definition.name ?? "extension tool", err instanceof Error ? err.message : String(err));
			}
		}
	}

	setHookWidget(key: string, content: unknown, options?: { placement?: "aboveEditor" | "belowEditor" }): void {
		const placement = options?.placement ?? "aboveEditor";
		this.#removeHookWidget(this.#hookWidgetsAbove, key);
		this.#removeHookWidget(this.#hookWidgetsBelow, key);
		if (content === undefined) {
			this.#rebuildHookWidgets();
			return;
		}
		const target = placement === "belowEditor" ? this.#hookWidgetsBelow : this.#hookWidgetsAbove;
		target.set(key, this.#createHookWidget(content));
		this.#rebuildHookWidgets();
	}

	setHookStatus(key: string, text: string | undefined): void {
		if (this.ctx.isBackgrounded) return;
		this.ctx.statusLine.setHookStatus?.(key, text);
		this.ctx.ui.requestRender();
	}

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

	showExtensionError(extensionPath: string, error: string): void {
		this.ctx.chatContainer.addChild(new Text(theme.fg("error", `Extension "${extensionPath}" error: ${error}`), 1, 0));
		this.ctx.ui.requestRender();
	}
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

	#createInteractiveUiContext(): Record<string, unknown> {
		return {
			select: (title: string, options: string[], dialogOptions?: ExtensionUIDialogOptions) =>
				this.showHookSelector(title, options, dialogOptions),
			confirm: (title: string, message: string) => this.showHookConfirm(title, message),
			input: (title: string, placeholder?: string, dialogOptions?: ExtensionUIDialogOptions) =>
				this.showHookInput(title, placeholder, dialogOptions),
			notify: (message: string, type?: "info" | "warning" | "error") => this.showHookNotify(message, type),
			onTerminalInput: (handler: (data: string) => unknown) => this.addExtensionTerminalInputListener(handler),
			setStatus: (key: string, text?: string) => this.setHookStatus(key, text),
			setWorkingMessage: (message?: string) => this.ctx.setWorkingMessage(message),
			setWidget: (key: string, content: unknown, options?: { placement?: "aboveEditor" | "belowEditor" }) =>
				this.setHookWidget(key, content, options),
			setTitle: () => {},
			custom: <T>(
				factory: (
					tui: TUI,
					theme: Theme,
					keybindings: KeybindingsManager,
					done: (result: T) => void,
				) => (Component & { dispose?(): void }) | Promise<Component & { dispose?(): void }>,
				options?: { overlay?: boolean },
			) => this.showHookCustom(factory, options),
			setEditorText: (text: string) => this.ctx.editor.setText(String(text ?? "")),
			pasteToEditor: (text: string) => this.ctx.editor.handleInput?.(`\x1b[200~${String(text ?? "")}\x1b[201~`),
			getEditorText: () => this.ctx.editor.getText(),
			editor: (
				title: string,
				prefill?: string,
				dialogOptions?: ExtensionUIDialogOptions,
				editorOptions?: { promptStyle?: boolean },
			) => this.showHookEditor(title, prefill, dialogOptions, editorOptions),
			get theme() {
				return theme;
			},
			getAllThemes: async () => [],
			getTheme: async () => undefined,
			setTheme: async () => ({ success: false, error: "Theme changes are handled by the CLI settings UI" }),
			setFooter: () => {},
			setHeader: () => {},
			setEditorComponent: () => {},
			getToolsExpanded: () => this.ctx.toolOutputExpanded,
			setToolsExpanded: (expanded: boolean) => this.ctx.setToolsExpanded(Boolean(expanded)),
		};
	}

	#createExtensionActions(): Record<string, unknown> {
		return {
			sendMessage: (message: { content?: string; display?: boolean }, options?: Record<string, unknown>) => {
				const wasStreaming = this.ctx.session.isStreaming;
				Promise.resolve(this.ctx.session.promptCustomMessage?.(message, options))
					.then(() => this.#applyCustomMessageDisplay(wasStreaming, message?.display))
					.catch(error => this.showExtensionError("extension", `sendMessage failed: ${this.#errorMessage(error)}`));
			},
			sendUserMessage: (content: unknown, options?: Record<string, unknown>) => {
				const wasStreaming = this.ctx.session.isStreaming;
				const message = { content: String(content ?? ""), display: true };
				Promise.resolve(this.ctx.session.sendUserMessage?.(content, options) ?? this.ctx.session.promptCustomMessage?.(message, options))
					.then(() => this.#applyCustomMessageDisplay(wasStreaming, true))
					.catch(error => this.showExtensionError("extension", `sendUserMessage failed: ${this.#errorMessage(error)}`));
			},
			appendEntry: () => {},
			setLabel: () => {},
			getActiveTools: () => this.ctx.session.getActiveToolNames?.() ?? [],
			getAllTools: () => this.ctx.session.getAllToolNames?.() ?? this.ctx.session.getActiveToolNames?.() ?? [],
			setActiveTools: (names: string[]) => this.ctx.session.setActiveToolsByName?.(names),
			setModel: async (model: unknown) => {
				if (this.ctx.session.setModel) {
					await this.ctx.session.setModel(model);
					return true;
				}
				if (this.ctx.session.setModelTemporary) {
					await this.ctx.session.setModelTemporary(model);
					return true;
				}
				return false;
			},
			getThinkingLevel: () => this.ctx.session.thinkingLevel,
			setThinkingLevel: (level?: string, persist?: boolean) => this.ctx.session.setThinkingLevel?.(level, persist),
			getCommands: () => this.ctx.session.extensionRunner?.getRegisteredCommands?.(new Set()) ?? [],
			getSessionName: () => this.ctx.sessionManager.getSessionName?.(),
			setSessionName: (name: string) => this.ctx.sessionManager.setSessionName?.(String(name), "user"),
		};
	}

	#createExtensionContextActions(): Record<string, unknown> {
		return {
			getModel: () => this.ctx.session.model,
			isIdle: () => !this.ctx.session.isStreaming,
			abort: () => this.ctx.session.abort(),
			hasPendingMessages: () => this.ctx.session.queuedMessageCount > 0,
			hasQueuedMessages: () => this.ctx.session.queuedMessageCount > 0,
			shutdown: () => {},
			getContextUsage: () => this.ctx.session.getContextUsage?.(),
			compact: (instructionsOrOptions: unknown) => this.#compactSession(instructionsOrOptions),
			getSystemPrompt: () => this.ctx.session.systemPrompt,
			cwd: () => this.ctx.sessionManager.getCwd(),
		};
	}

	#createExtensionCommandActions(): Record<string, unknown> {
		return {
			getContextUsage: () => this.ctx.session.getContextUsage?.(),
			waitForIdle: async () => this.ctx.agent?.waitForIdle?.(),
			reload: async () => {
				this.ctx.rebuildChatFromMessages();
				await this.ctx.reloadTodos();
				this.ctx.showStatus("Reloaded session");
				return { cancelled: false };
			},
			newSession: async () => {
				const ok = await this.ctx.session.newSession?.();
				if (!ok) return { cancelled: true };
				this.ctx.rebuildChatFromMessages();
				this.ctx.clearEditor();
				return { cancelled: false };
			},
			branch: async () => ({ cancelled: true }),
			navigateTree: async () => ({ cancelled: true }),
			compact: async (instructionsOrOptions: unknown) => {
				await this.#compactSession(instructionsOrOptions);
				return { cancelled: false };
			},
			switchSession: async (sessionPath: string) => {
				if (!this.ctx.session.switchSession) return { cancelled: true };
				const ok = await this.ctx.session.switchSession(sessionPath);
				if (!ok) return { cancelled: true };
				this.ctx.rebuildChatFromMessages();
				return { cancelled: false };
			},
		};
	}

	#removeHookWidget(map: Map<string, Component & { dispose?(): void }>, key: string): void {
		const previous = map.get(key);
		previous?.dispose?.();
		map.delete(key);
	}

	#createHookWidget(content: unknown): Component & { dispose?(): void } {
		if (this.#isComponent(content)) return content;
		if (typeof content === "function") {
			const produced = content(this.ctx.ui, theme);
			if (this.#isComponent(produced)) return produced;
			return new Text(String(produced ?? ""), 1, 0);
		}
		if (Array.isArray(content)) {
			return new Text(content.slice(0, MAX_WIDGET_LINES).map(item => String(item)).join("\n"), 1, 0);
		}
		if (content && typeof content === "object") {
			const maybeText = "text" in content ? (content as { text?: unknown }).text : undefined;
			return new Text(String(maybeText ?? JSON.stringify(content)), 1, 0);
		}
		return new Text(String(content ?? ""), 1, 0);
	}

	#rebuildHookWidgets(): void {
		this.#renderHookWidgetContainer(this.ctx.hookWidgetContainerAbove, this.#hookWidgetsAbove, true);
		this.#renderHookWidgetContainer(this.ctx.hookWidgetContainerBelow, this.#hookWidgetsBelow, false);
		this.ctx.ui.requestRender();
	}

	#renderHookWidgetContainer(
		container: Container,
		widgets: Map<string, Component & { dispose?(): void }>,
		spacerWhenEmpty: boolean,
	): void {
		container.clear();
		if (widgets.size === 0) {
			if (spacerWhenEmpty) container.addChild(new Spacer(1));
			return;
		}
		for (const widget of widgets.values()) container.addChild(widget);
	}

	async #compactSession(instructionsOrOptions?: unknown): Promise<void> {
		if (this.ctx.executeCompaction) {
			await this.ctx.executeCompaction(instructionsOrOptions as string | Record<string, unknown>);
			return;
		}
		const instructions = typeof instructionsOrOptions === "string" ? instructionsOrOptions : undefined;
		const options = instructionsOrOptions && typeof instructionsOrOptions === "object" ? instructionsOrOptions : undefined;
		if (this.ctx.session.compact) {
			await this.ctx.session.compact(instructions, options);
			this.ctx.rebuildChatFromMessages();
			return;
		}
		this.ctx.showWarning("Compaction is handled by the kernel and is not available through this UI yet.");
	}

	#applyCustomMessageDisplay(wasStreaming: boolean, display?: boolean): void {
		if (this.ctx.isBackgrounded || wasStreaming || display === false) return;
		this.ctx.rebuildChatFromMessages();
		this.ctx.ui.requestRender();
	}

	#isComponent(value: unknown): value is Component & { dispose?(): void } {
		return !!value && typeof value === "object" && typeof (value as Component).render === "function";
	}

	#errorMessage(error: unknown): string {
		return error instanceof Error ? error.message : String(error);
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
