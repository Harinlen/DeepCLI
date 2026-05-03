import { KeybindingsManager } from "../src/active-port/coding-agent/config/keybindings.js";
import { executeBuiltinSlashCommand } from "../src/active-port/coding-agent/slash-commands/builtin-registry.js";
import { getCurrentThemeName, initTheme } from "../src/active-port/coding-agent/modes/theme/theme.js";
import { assert } from "./helpers.js";

const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { InputController } = await load("../src/active-port/coding-agent/modes/controllers/input-controller.ts");
const { CommandController } = await load("../src/active-port/coding-agent/modes/controllers/command-controller.ts");
const { AssistantMessageComponent } = await load("../src/active-port/coding-agent/modes/components/assistant-message.ts");

await initTheme(false, "unicode", false, "dark", "dark");

class FakeEditor {
	text = "";
	history: string[] = [];
	onSubmit?: (text: string) => Promise<void>;
	onChange?: (text: string) => void;
	onEscape?: () => void;
	shouldBypassAutocompleteOnEscape?: () => boolean;
	onClear?: () => void;
	onExit?: () => void;
	onHistorySearch?: () => void;
	onDequeue?: () => void;
	onShowHotkeys?: () => void;

	setActionKeys() {}
	clearCustomKeyHandlers() {}
	setCustomKeyHandler() {}
	setText(value: string) {
		this.text = value;
		this.onChange?.(value);
	}
	getText() { return this.text; }
	addToHistory(value: string) { this.history.push(value); }
}

function makeContext() {
	const calls: string[] = [];
	const inputListeners: Array<(data: string) => { consume?: boolean } | undefined> = [];
	const editor = new FakeEditor();
	const session = {
		isStreaming: false,
		isCompacting: false,
		isGeneratingHandoff: false,
		isBashRunning: false,
		isPythonRunning: false,
		queuedMessageCount: 0,
		messages: [],
		extensionRunner: undefined,
		clearQueue: () => ({ steering: [], followUp: [] }),
		abort: () => calls.push("abort"),
		abortBash: () => calls.push("abort-bash"),
		abortPython: () => calls.push("abort-python"),
		executeBash: async (command: string, onChunk: (chunk: string) => void, options: { excludeFromContext?: boolean }) => {
			calls.push(`bash:${command}:${options.excludeFromContext ? "excluded" : "context"}`);
			onChunk("bash-output");
			return { exitCode: 0, cancelled: false, output: "bash-output" };
		},
		executePython: async (code: string, onChunk: (chunk: string) => void, options: { excludeFromContext?: boolean }) => {
			calls.push(`python:${code}:${options.excludeFromContext ? "excluded" : "context"}`);
			onChunk("python-output");
			return { exitCode: 0, cancelled: false, output: "python-output" };
		},
		sessionManager: {
			setSessionName: async () => true,
		},
		deleteCurrentSessionAndCreate: async () => {
			calls.push("session-delete-confirm");
			return "new-session";
		},
	};
	const ctx: any = {
		editor,
		session,
		keybindings: KeybindingsManager.inMemory(),
		loadingAnimation: undefined,
		autoCompactionLoader: undefined,
		retryLoader: undefined,
		autoCompactionEscapeHandler: undefined,
		retryEscapeHandler: undefined,
		lastEscapeTime: 0,
		lastSigintTime: 0,
		isBashMode: false,
		isPythonMode: false,
		pendingImages: [],
		pendingBashComponents: [],
		pendingPythonComponents: [],
		bashComponent: undefined,
		pythonComponent: undefined,
		ui: {
			requestRender: () => calls.push("render"),
			onDebug: undefined,
			hasOverlay: () => false,
			addInputListener: (listener: (data: string) => { consume?: boolean } | undefined) => {
				inputListeners.push(listener);
				return () => {};
			},
		},
		toolOutputExpanded: false,
		chatContainer: {
			children: [{ setExpanded: (expanded: boolean) => calls.push(`expand:${expanded}`) }],
			addChild: () => calls.push("chat-add"),
		},
		pendingMessagesContainer: { addChild: () => calls.push("pending-add") },
		hasActiveBtw: () => false,
		handleBtwEscape: () => false,
		updateEditorBorderColor: () => calls.push(`border:${ctx.isBashMode ? "bash" : ctx.isPythonMode ? "python" : "normal"}`),
		updateEditorTopBorder: () => calls.push("top-border"),
		showTreeSelector: () => calls.push("tree"),
		showUserMessageSelector: () => calls.push("user-message-selector"),
		showModelSelector: () => calls.push("model-selector"),
		showDebugSelector: () => calls.push("debug-selector"),
		showHistorySearch: () => calls.push("history-search"),
		toggleThinkingBlockVisibility: () => calls.push("thinking-toggle"),
		handleHotkeysCommand: () => calls.push("hotkeys"),
		handlePlanModeCommand: () => calls.push("plan"),
		handleCompactCommand: () => calls.push("compact"),
		handleUsageCommand: () => calls.push("usage"),
		handleMemoryCommand: (text: string) => calls.push(`memory:${text}`),
		handleClearCommand: () => calls.push("clear-command"),
		showSessionSelector: () => calls.push("session-selector"),
		handleSTTToggle: () => calls.push("stt"),
		showSessionObserver: () => calls.push("session-observer"),
		clearEditor: () => {
			editor.setText("");
			calls.push("clear-editor");
		},
		shutdown: () => calls.push("shutdown"),
		showWarning: (message: string) => calls.push(`warning:${message}`),
		showStatus: (message: string) => calls.push(`status:${message}`),
		showError: (message: string) => calls.push(`error:${message}`),
		flushPendingBashComponents: () => calls.push("flush-bash"),
		updatePendingMessagesDisplay: () => calls.push("pending-display"),
		queueCompactionMessage: (text: string) => calls.push(`queue:${text}`),
		handleBashCommand: async (command: string, excluded: boolean) => {
			await session.executeBash(command, () => {}, { excludeFromContext: excluded });
		},
		handlePythonCommand: async (code: string, excluded: boolean) => {
			await session.executePython(code, () => {}, { excludeFromContext: excluded });
		},
	};
	return { ctx, editor, calls, inputListeners };
}

const { ctx, editor, calls, inputListeners } = makeContext();
const controller = new InputController(ctx);
controller.setupKeyHandlers();
controller.setupEditorSubmitHandler();

const expandResult = inputListeners[0]?.("\x0f");
assert(expandResult?.consume === true, "TUI-level Ctrl+O handler should consume the expand shortcut");
assert(ctx.toolOutputExpanded === true, "TUI-level Ctrl+O should toggle tool output expansion");
assert(calls.includes("expand:true"), "TUI-level Ctrl+O should update expandable chat components");

let thinkingHideValue: boolean | undefined;
let thinkingInvalidated: boolean = false;
const thinkingComponent = new AssistantMessageComponent({
	role: "assistant",
	content: [{ type: "thinking", thinking: "trace" }],
	timestamp: 0,
} as never, false);
thinkingComponent.setHideThinkingBlock = (hide: boolean) => {
	thinkingHideValue = hide;
};
thinkingComponent.invalidate = () => {
	thinkingInvalidated = true;
};
ctx.chatContainer.children.push(thinkingComponent);
ctx.streamingComponent = thinkingComponent;
ctx.streamingMessage = {
	role: "assistant",
	content: [{ type: "thinking", thinking: "trace" }],
	timestamp: 0,
};
controller.toggleThinkingBlockVisibility();
assert(ctx.hideThinkingBlock === true, "thinking toggle should update hidden flag");
assert(thinkingHideValue === true, "thinking toggle should update existing assistant components");
assert(thinkingInvalidated, "thinking toggle should re-render existing assistant components");
assert(!calls.includes("chat-clear"), "thinking toggle must not clear and rebuild the chat");
assert(calls.some(item => item === "status:Thinking blocks: hidden"), "thinking toggle should report hidden state");

editor.setText("!");
assert(ctx.isBashMode, "typing ! should enter bash mode");
assert(calls.includes("border:bash"), "! should refresh bash border color");
editor.setText("$");
assert(ctx.isPythonMode, "typing $ should enter python mode");
assert(calls.includes("border:python"), "$ should refresh python border color");

await editor.onSubmit?.("! pwd");
assert(calls.includes("bash:pwd:context"), "! should route through session.executeBash");
await editor.onSubmit?.("!! env");
assert(calls.includes("bash:env:excluded"), "!! should exclude bash output from context");
await editor.onSubmit?.("$ print(1)");
assert(calls.includes("python:print(1):context"), "$ should route through session.executePython");
await editor.onSubmit?.("$$ x = 1");
assert(calls.includes("python:x = 1:excluded"), "$$ should exclude python output from context");

let submittedPrompt = "";
ctx.onInputCallback = (submission: { text: string }) => {
	submittedPrompt = submission.text;
	calls.push(`prompt:${submission.text}`);
};
await editor.onSubmit?.("/auth");
assert(calls.some(item => item === "warning:/auth is not wired in the CLI TUI yet."), "/auth should fail locally until wired");
assert(submittedPrompt === "", "/auth must not fall through to the LLM prompt path");
ctx.onInputCallback = undefined;

ctx.session.isBashRunning = true;
editor.onEscape?.();
assert(calls.includes("abort-bash"), "Escape should cancel running bash command");
ctx.session.isBashRunning = false;
ctx.isBashMode = true;
editor.setText("! pending");
editor.onEscape?.();
assert(editor.getText() === "", "Escape should clear bash-mode editor text");
assert(ctx.isBashMode === false, "Escape should leave bash mode");

ctx.session.isPythonRunning = true;
editor.onEscape?.();
assert(calls.includes("abort-python"), "Escape should cancel running python command");
ctx.session.isPythonRunning = false;
ctx.session.isStreaming = true;
editor.onEscape?.();
assert(calls.includes("abort"), "Escape should abort active stream");
ctx.session.isStreaming = false;

editor.setText("draft");
editor.onClear?.();
assert(editor.getText() === "", "First Ctrl+C should clear editor");
editor.onClear?.();
assert(calls.includes("shutdown"), "Second Ctrl+C should request shutdown");

const deleteCalls: string[] = [];
const deleteCtx = {
	session: {
		deleteCurrentSessionAndCreate: async () => {
			deleteCalls.push("delete");
			return "new-session";
		},
	},
	showWarning: (message: string) => deleteCalls.push(`warning:${message}`),
	showStatus: (message: string) => deleteCalls.push(`status:${message}`),
	updateEditorTopBorder: () => deleteCalls.push("top-border"),
};
await executeBuiltinSlashCommand("/session delete", { ctx: deleteCtx });
assert(deleteCalls.some(item => item.startsWith("warning:")), "/session delete should require confirm");
assert(!deleteCalls.includes("delete"), "/session delete without confirm must not delete");
await executeBuiltinSlashCommand("/session delete confirm", { ctx: deleteCtx });
assert(deleteCalls.includes("delete"), "/session delete confirm should call the ACP delete path");

const listCalls: string[] = [];
const listCtx = {
	session: {
		listSessions: async () => [
			{ sessionId: "sess-1", title: "Alpha", cwd: "/repo/a" },
			{ sessionId: "sess-2", title: "Beta", cwd: "/repo/b" },
		],
		loadSession: async (id: string) => {
			listCalls.push(`load:${id}`);
			return id;
		},
	},
	showSessionSelector: () => listCalls.push("session-selector"),
	showStatus: (message: string) => listCalls.push(`status:${message}`),
	showWarning: (message: string) => listCalls.push(`warning:${message}`),
	updateEditorTopBorder: () => listCalls.push("top-border"),
};
await executeBuiltinSlashCommand("/session list", { ctx: listCtx });
assert(listCalls.includes("session-selector"), "/session list should open the OMP-backed session selector");
assert(!listCalls.some(item => item.startsWith("status:") && item.includes("\n")), "/session list must not put multiline output in status");
await executeBuiltinSlashCommand("/session switch 2", { ctx: listCtx });
assert(listCalls.includes("load:sess-2"), "/session switch <number> should resolve through the ACP session list");

const failClosedCalls: string[] = [];
await executeBuiltinSlashCommand("/session", {
	ctx: {
		handleSessionCommand: async () => {
			throw new Error("missing compat");
		},
		showError: (message: string) => failClosedCalls.push(`error:${message}`),
	},
});
assert(
	failClosedCalls.includes("error:/session failed: missing compat"),
	"builtin slash commands should fail through the TUI error path",
);

const themeCalls: string[] = [];
const themeCtx = {
	enableThemeWatcher: false,
	statusLine: { invalidate: () => themeCalls.push("status-invalidate") },
	ui: {
		invalidate: () => themeCalls.push("ui-invalidate"),
		requestRender: () => themeCalls.push("render"),
	},
	updateEditorTopBorder: () => themeCalls.push("top-border"),
	updateEditorBorderColor: () => themeCalls.push("border"),
	showStatus: (message: string) => themeCalls.push(`status:${message}`),
	showWarning: (message: string) => themeCalls.push(`warning:${message}`),
	showError: (message: string) => themeCalls.push(`error:${message}`),
};
await executeBuiltinSlashCommand("/theme current", { ctx: themeCtx });
assert(themeCalls.includes("status:Current theme: dark"), "/theme current should show the active theme");
await executeBuiltinSlashCommand("/theme list", { ctx: themeCtx });
assert(
	themeCalls.some(item => item.startsWith("status:Available themes:") && item.includes("* dark")),
	"/theme list should list available themes and mark the current theme when no selector is available",
);
const themeSelectorCalls: string[] = [];
await executeBuiltinSlashCommand("/theme list", {
	ctx: {
		showThemeSelector: () => themeSelectorCalls.push("theme-selector"),
		showStatus: (message: string) => themeSelectorCalls.push(`status:${message}`),
	},
});
assert(themeSelectorCalls.includes("theme-selector"), "/theme list should open the OMP theme selector in the TUI");
assert(
	!themeSelectorCalls.some(item => item.startsWith("status:Available themes:")),
	"/theme list should not dump the theme list into the status area when the selector exists",
);
await executeBuiltinSlashCommand("/theme set light", { ctx: themeCtx });
assert(getCurrentThemeName() === "light", "/theme set should switch the active theme");
assert(themeCalls.includes("status:Theme set to light"), "/theme set should report success");
assert(themeCalls.includes("status-invalidate"), "/theme set should refresh the status line");
assert(themeCalls.includes("top-border"), "/theme set should refresh the editor top border");
await executeBuiltinSlashCommand("/theme set definitely-missing-theme", { ctx: themeCtx });
assert(
	themeCalls.some(item => item.startsWith('error:Failed to load theme "definitely-missing-theme"')),
	"/theme set should report invalid theme errors",
);

const clearCalls: string[] = [];
const clearCtx = {
	loadingAnimation: undefined,
	statusContainer: { clear: () => clearCalls.push("status-clear") },
	session: {
		isCompacting: false,
		newSession: async () => clearCalls.push("new-session"),
	},
	statusLine: {
		invalidate: () => clearCalls.push("status-invalidate"),
		setSessionStartTime: () => clearCalls.push("session-start-time"),
	},
	updateEditorTopBorder: () => clearCalls.push("top-border"),
	updateEditorBorderColor: () => clearCalls.push("border"),
	chatContainer: {
		clear: () => clearCalls.push("chat-clear"),
		addChild: () => clearCalls.push("chat-add"),
	},
	pendingMessagesContainer: { clear: () => clearCalls.push("pending-clear") },
	compactionQueuedMessages: ["queued"],
	streamingComponent: {},
	streamingMessage: {},
	pendingTools: { clear: () => clearCalls.push("tools-clear") },
	showStatus: (message: string) => clearCalls.push(`status:${message}`),
	ui: { requestRender: () => clearCalls.push("render") },
	resetObserverRegistry: () => clearCalls.push("reset-observers"),
	reloadTodos: async () => clearCalls.push("reload-todos"),
};
await new CommandController(clearCtx).handleClearCommand();
assert(clearCalls.includes("chat-clear"), "/clear should clear the visible chat container");
assert(clearCalls.includes("pending-clear"), "/clear should clear pending visible messages");
assert(clearCalls.includes("status:Conversation view cleared"), "/clear should report a view clear");
assert(!clearCalls.includes("new-session"), "/clear must not create a new session");
assert(!clearCalls.includes("reset-observers"), "/clear must not reset session observers");
assert(!clearCalls.includes("session-start-time"), "/clear must not reset session timing");

console.log("PASS: input controller R4");
