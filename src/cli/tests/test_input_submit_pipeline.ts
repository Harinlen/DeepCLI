import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { KeybindingsManager } from "../src/active-port/coding-agent/config/keybindings.js";
import { initTheme } from "../src/active-port/coding-agent/modes/theme/theme.js";
import { assert } from "./helpers.js";

const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { InputController } = await load("../src/active-port/coding-agent/modes/controllers/input-controller.ts");

await initTheme(false, "unicode", false, "dark", "dark");

class FakeEditor {
	text = "";
	history: string[] = [];
	onSubmit?: (text: string) => Promise<void>;
	onChange?: (text: string) => void;
	setText(value: string) {
		this.text = value;
		this.onChange?.(value);
	}
	getText() {
		return this.text;
	}
	addToHistory(value: string) {
		this.history.push(value);
	}
}

function makeContext() {
	const calls: string[] = [];
	const editor = new FakeEditor();
	const session: any = {
		isStreaming: false,
		isCompacting: false,
		isBashRunning: false,
		isPythonRunning: false,
		queuedMessageCount: 0,
		messages: [{ role: "user" }],
		extensionRunner: undefined,
		prompt: async (text: string, options: unknown) => {
			calls.push(`prompt:${text}:${JSON.stringify(options)}`);
		},
		promptCustomMessage: async (message: any, options: unknown) => {
			calls.push(`skill:${message.details.name}:${message.details.args ?? ""}:${JSON.stringify(options)}`);
		},
		abort: async () => calls.push("abort"),
	};
	const ctx: any = {
		editor,
		session,
		keybindings: KeybindingsManager.inMemory(),
		pendingImages: [],
		pendingBashComponents: [],
		pendingPythonComponents: [],
		skillCommands: new Map<string, string>(),
		isBashMode: false,
		isPythonMode: false,
		sessionManager: {
			getSessionName: () => "Test",
			getCwd: () => "/tmp",
			titleSource: "user",
		},
		ui: { requestRender: () => calls.push("render") },
		statusContainer: { clear: () => calls.push("status-clear") },
		statusLine: { invalidate: () => calls.push("status-invalidate") },
		flushPendingBashComponents: () => calls.push("flush-bash"),
		updateEditorBorderColor: () => calls.push("border"),
		updatePendingMessagesDisplay: () => calls.push("pending-display"),
		refreshWelcomeRecentSessions: async () => calls.push("refresh-recent"),
		queueCompactionMessage: (text: string, kind: string) => calls.push(`queue:${text}:${kind}`),
		handleBashCommand: async (command: string, excluded: boolean) => calls.push(`bash:${command}:${excluded}`),
		handlePythonCommand: async (code: string, excluded: boolean) => calls.push(`python:${code}:${excluded}`),
		handleHotkeysCommand: () => calls.push("hotkeys"),
		showStatus: (message: string) => calls.push(`status:${message}`),
		showWarning: (message: string) => calls.push(`warning:${message}`),
		showError: (message: string) => calls.push(`error:${message}`),
		startPendingSubmission: ({ text, images }: { text: string; images?: unknown[] }) => {
			calls.push(`start:${text}:${images?.length ?? 0}`);
			return { text, images };
		},
		onInputCallback: (submission: { text: string }) => calls.push(`input:${submission.text}`),
	};
	new InputController(ctx).setupEditorSubmitHandler();
	return { ctx, editor, session, calls };
}

{
	const { editor, session, calls } = makeContext();
	session.extensionRunner = {
		hasHandlers: (event: string) => event === "input",
		emitInput: async () => {
			calls.push("extension:handled");
			return { handled: true };
		},
	};
	await editor.onSubmit?.("hello");
	assert(calls.includes("extension:handled"), "extension input handler should run first");
	assert(!calls.some(call => call.startsWith("input:")), "handled extension input must not reach normal prompt");
}

{
	const { editor, session, calls } = makeContext();
	session.extensionRunner = {
		hasHandlers: (event: string) => event === "input",
		emitInput: async () => ({ text: "/help" }),
	};
	await editor.onSubmit?.("please help");
	assert(calls.includes("hotkeys"), "extension-rewritten text should enter builtin slash dispatch");
	assert(!calls.some(call => call.startsWith("input:")), "builtin slash should consume extension-rewritten input");
}

{
	const { editor, calls } = makeContext();
	await editor.onSubmit?.("/unknown command");
	assert(calls.includes("input:/unknown command"), "unknown slash command should fall through to normal prompt");
}

{
	const { editor, calls } = makeContext();
	await editor.onSubmit?.("new message");
	assert(calls.includes("status-clear"), "submitting new input should clear previous transient status text");
	assert(calls.includes("input:new message"), "status clearing must not consume the submitted message");
}

{
	const { ctx, editor, calls } = makeContext();
	const dir = mkdtempSync(path.join(tmpdir(), "deepcli-skill-"));
	const skillPath = path.join(dir, "SKILL.md");
	writeFileSync(skillPath, "---\nname: test\n---\nSkill body\n", "utf8");
	ctx.skillCommands.set("skill:test", skillPath);
	await editor.onSubmit?.("/skill:test with args");
	assert(calls.includes("skill:test:with args:{\"streamingBehavior\":\"followUp\"}"), "skill command should become a custom skill prompt");
}

{
	const { editor, session, calls } = makeContext();
	session.isCompacting = true;
	await editor.onSubmit?.("queue me");
	assert(calls.includes("queue:queue me:steer"), "compacting session should queue prompt steering text");
}

{
	const { ctx, editor, session, calls } = makeContext();
	session.isStreaming = true;
	ctx.pendingImages = [{ id: "img" }];
	await editor.onSubmit?.("steer me");
	assert(
		calls.includes("prompt:steer me:{\"streamingBehavior\":\"steer\",\"images\":[{\"id\":\"img\"}]}"),
		"streaming session should send prompt with steer behavior and pending images",
	);
	assert(ctx.pendingImages.length === 0, "streaming submit should clear pending images after sending");
}

{
	const { editor, calls } = makeContext();
	await editor.onSubmit?.("! pwd");
	await editor.onSubmit?.("$ print(1)");
	assert(calls.includes("bash:pwd:false"), "! input should route to bash before normal prompt");
	assert(calls.includes("python:print(1):false"), "$ input should route to python before normal prompt");
}

console.log("PASS: input submit pipeline ordering");
