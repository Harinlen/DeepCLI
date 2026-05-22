import { assert } from "./helpers.js";

const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { initTheme, getEditorTheme } = await load("../src/active-port/coding-agent/modes/theme/theme.ts");
const { CustomEditor } = await load("../src/active-port/coding-agent/modes/components/custom-editor.ts");
const { KeybindingsManager } = await load("../src/active-port/coding-agent/config/keybindings.ts");

await initTheme(false);

const keybindings = KeybindingsManager.inMemory({
	"app.thinking.cycle": "alt+t",
});
const editor = new CustomEditor(getEditorTheme());
const calls: string[] = [];

for (const action of [
	"app.interrupt",
	"app.clear",
	"app.exit",
	"app.suspend",
	"app.thinking.cycle",
	"app.permissionMode.cycle",
	"app.model.cycleForward",
	"app.model.cycleBackward",
	"app.model.selectTemporary",
	"app.model.select",
	"app.history.search",
	"app.thinking.toggle",
	"app.editor.external",
	"app.clipboard.pasteImage",
	"app.clipboard.copyPrompt",
	"app.tools.expand",
	"app.message.dequeue",
] as const) {
	editor.setActionKeys(action, keybindings.getKeys(action));
}

editor.onEscape = () => calls.push("interrupt");
editor.onClear = () => calls.push("clear");
editor.onExit = () => calls.push("exit");
editor.onSuspend = () => calls.push("suspend");
editor.onCycleThinkingLevel = () => calls.push("thinking-cycle");
editor.onCyclePermissionMode = () => calls.push("permission-cycle");
editor.onCycleModelForward = () => calls.push("model-forward");
editor.onCycleModelBackward = () => calls.push("model-backward");
editor.onSelectModelTemporary = () => calls.push("model-temporary");
editor.onSelectModel = () => calls.push("model-select");
editor.onHistorySearch = () => calls.push("history");
editor.onToggleThinking = () => calls.push("thinking-toggle");
editor.onExternalEditor = () => calls.push("external-editor");
editor.onPasteImage = async () => {
	calls.push("paste-image");
	return true;
};
editor.onCopyPrompt = () => calls.push("copy-prompt");
editor.onExpandTools = () => calls.push("expand-tools");
editor.onDequeue = () => calls.push("dequeue");
editor.onShowHotkeys = () => calls.push("hotkeys");

const cases: Array<[string, string, string]> = [
	["escape", "\x1b", "interrupt"],
	["escape modifyOtherKeys", "\x1b[27;1;27~", "interrupt"],
	["ctrl+c", "\x03", "clear"],
	["ctrl+d", "\x04", "exit"],
	["ctrl+z", "\x1a", "suspend"],
	["alt+t", "\x1bt", "thinking-cycle"],
	["alt+t modifyOtherKeys", "\x1b[27;3;116~", "thinking-cycle"],
	["shift+tab", "\x1b[Z", "permission-cycle"],
	["shift+tab CSI-u", "\x1b[9;2u", "permission-cycle"],
	["ctrl+p", "\x10", "model-forward"],
	["shift+ctrl+p CSI-u", "\x1b[80;6u", "model-backward"],
	["shift+ctrl+p modifyOtherKeys", "\x1b[27;6;80~", "model-backward"],
	["alt+p", "\x1bp", "model-temporary"],
	["alt+p modifyOtherKeys", "\x1b[27;3;112~", "model-temporary"],
	["ctrl+l", "\x0c", "model-select"],
	["ctrl+r", "\x12", "history"],
	["ctrl+t", "\x14", "thinking-toggle"],
	["ctrl+g", "\x07", "external-editor"],
	["ctrl+v", "\x16", "paste-image"],
	["alt+shift+c", "\x1bC", "copy-prompt"],
	["ctrl+o", "\x0f", "expand-tools"],
	["ctrl+o modifyOtherKeys", "\x1b[27;5;111~", "expand-tools"],
	["alt+up", "\x1b[1;3A", "dequeue"],
	["?", "?", "hotkeys"],
];

for (const [label, sequence, expected] of cases) {
	editor.setText("");
	calls.length = 0;
	editor.handleInput(sequence);
	await Promise.resolve();
	assert(calls.includes(expected), `${label} should trigger ${expected}; calls=${calls.join(",")}`);
}

editor.setText("not empty");
calls.length = 0;
editor.handleInput("\x04");
assert(!calls.includes("exit"), "Ctrl+D should not exit when the prompt editor has text");

const customCalls: string[] = [];
editor.setCustomKeyHandler("alt+shift+p", () => customCalls.push("plan-toggle"));
editor.setCustomKeyHandler("ctrl+enter", () => customCalls.push("follow-up"));
editor.setCustomKeyHandler("alt+h", () => customCalls.push("stt"));
editor.setCustomKeyHandler("alt+shift+l", () => customCalls.push("copy-line"));
editor.setCustomKeyHandler("ctrl+s", () => customCalls.push("session-observe"));

for (const [label, sequence, expected] of [
	["alt+shift+p", "\x1bP", "plan-toggle"],
	["alt+shift+p modifyOtherKeys", "\x1b[27;4;80~", "plan-toggle"],
	["ctrl+enter CSI-u", "\x1b[13;5u", "follow-up"],
	["alt+h", "\x1bh", "stt"],
	["alt+h modifyOtherKeys", "\x1b[27;3;104~", "stt"],
	["alt+shift+l", "\x1bL", "copy-line"],
	["ctrl+s", "\x13", "session-observe"],
] as const) {
	customCalls.length = 0;
	editor.handleInput(sequence);
	assert(customCalls.includes(expected), `${label} should trigger ${expected}`);
}

console.log("PASS: CLI shortcut raw key matrix");
