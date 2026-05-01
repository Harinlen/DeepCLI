import { KeybindingsManager } from "../src/active-port/coding-agent/config/keybindings.js";
import { executeBuiltinSlashCommand } from "../src/active-port/coding-agent/slash-commands/builtin-registry.js";
import { assert } from "./helpers.js";

const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { InputController } = await load("../src/active-port/coding-agent/modes/controllers/input-controller.ts");

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
		ui: { requestRender: () => calls.push("render"), onDebug: undefined },
		chatContainer: { addChild: () => calls.push("chat-add") },
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
	return { ctx, editor, calls };
}

const { ctx, editor, calls } = makeContext();
const controller = new InputController(ctx);
controller.setupKeyHandlers();
controller.setupEditorSubmitHandler();

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

console.log("PASS: input controller R4");
