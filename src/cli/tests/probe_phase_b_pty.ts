import { spawn } from "node:child_process";
import { WebSocketServer, WebSocket } from "ws";
import { assert } from "./helpers.js";

type Json = Record<string, any>;

const bunBin = process.env.BUN_BIN ?? Bun.which("bun") ?? `${process.env.HOME}/.bun/bin/bun`;
const token = "phase-b-pty-token";
const methods = {
	modelProfileList: "_mustang.agent/model/profile_list",
	modelProviderList: "_mustang.agent/model/provider_list",
	modelSetDefault: "_mustang.agent/model/set_default",
	sessionExecuteShell: "_mustang.agent/session/execute_shell",
	sessionExecutePython: "_mustang.agent/session/execute_python",
	sessionCancelExecution: "_mustang.agent/session/cancel_execution",
	sessionExecutionUpdate: "_mustang.agent/session/execution_update",
	sessionDelete: "_mustang.agent/session/delete",
	sessionRename: "_mustang.agent/session/rename",
	sessionArchive: "_mustang.agent/session/archive",
} as const;

class FakeAcpKernel {
	#server?: WebSocketServer;
	#sessionCounter = 0;
	#permissionRequestId = 10_000;
	#pendingServerRequests = new Map<number, (result: unknown) => void>();
	calls: string[] = [];
	url = "";
	permissionOutcome: string | undefined;

	async start(): Promise<void> {
		this.#server = new WebSocketServer({ port: 0 });
		this.#server.on("connection", ws => this.#handleConnection(ws));
		await new Promise<void>(resolve => this.#server!.once("listening", resolve));
		const address = this.#server.address();
		if (!address || typeof address === "string") throw new Error("Could not resolve fake kernel port");
		this.url = `ws://127.0.0.1:${address.port}`;
	}

	async stop(): Promise<void> {
		const server = this.#server;
		if (!server) return;
		await new Promise<void>(resolve => server.close(() => resolve()));
	}

	#handleConnection(ws: WebSocket): void {
		ws.on("message", raw => {
			void this.#handleMessage(ws, JSON.parse(raw.toString()) as Json);
		});
	}

	async #handleMessage(ws: WebSocket, message: Json): Promise<void> {
		if ("id" in message && "result" in message && !message.method) {
			const resolve = this.#pendingServerRequests.get(Number(message.id));
			this.#pendingServerRequests.delete(Number(message.id));
			resolve?.(message.result);
			return;
		}

		const id = Number(message.id);
		const method = String(message.method ?? "");
		this.calls.push(method);
		const params = message.params as Json ?? {};
		const sessionId = String(params.sessionId ?? "pty-session-1");

		switch (method) {
			case "initialize":
				return this.#result(ws, id, { protocolVersion: 1, serverInfo: { name: "fake-kernel", version: "0" } });
			case "session/new": {
				this.#sessionCounter += 1;
				return this.#result(ws, id, { sessionId: `pty-session-${this.#sessionCounter}`, configOptions: [], modes: [{ id: "default", name: "Default" }] });
			}
			case "session/list":
				return this.#result(ws, id, {
					sessions: [
						{ sessionId: "recent-1", title: "Recent session", cwd: process.cwd(), updatedAt: new Date().toISOString() },
						{ sessionId: "recent-2", title: "Second session", cwd: `${process.cwd()}/src/cli`, updatedAt: new Date().toISOString() },
					],
					nextCursor: null,
				});
			case "session/load":
				return this.#result(ws, id, { sessionId, session: { sessionId, title: sessionId, cwd: process.cwd() } });
			case methods.modelProfileList:
				return this.#result(ws, id, { profiles: [], defaultModel: "" });
			case methods.modelProviderList:
				return this.#result(ws, id, { providers: [] });
			case methods.modelSetDefault:
				return this.#result(ws, id, { defaultModel: "fake/model" });
			case "session/prompt":
				await this.#handlePrompt(ws, id, sessionId, promptText(params.prompt));
				return;
			case methods.sessionExecuteShell:
				this.#notifyExecution(ws, sessionId, { phase: "chunk", kind: "shell", executionId: "shell-1", stream: "stdout", text: "PTY_SHELL\n" });
				return this.#result(ws, id, { exitCode: 0, cancelled: false });
			case methods.sessionExecutePython:
				this.#notifyExecution(ws, sessionId, { phase: "chunk", kind: "python", executionId: "python-1", stream: "stdout", text: "PTY_PY\n" });
				return this.#result(ws, id, { exitCode: 0, cancelled: false });
			case methods.sessionDelete:
				return this.#result(ws, id, { deleted: true });
			case methods.sessionRename:
				return this.#result(ws, id, { session: { sessionId, title: String(params.title ?? "renamed"), cwd: process.cwd(), titleSource: "user" } });
			case methods.sessionArchive:
				return this.#result(ws, id, { session: { sessionId, title: sessionId, cwd: process.cwd(), archivedAt: params.archived ? new Date().toISOString() : null } });
			case "session/cancel":
			case methods.sessionCancelExecution:
				return;
			default:
				return this.#result(ws, id, {});
		}
	}

	async #handlePrompt(ws: WebSocket, id: number, sessionId: string, text: string): Promise<void> {
		if (text.includes("slow stream")) {
			this.#notify(ws, sessionId, { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "slow before" } });
			await sleep(800);
			this.#notify(ws, sessionId, { sessionUpdate: "tool_call", toolCallId: "slow-tool-1", title: "slowbash", rawInput: "{\"command\":\"echo slow\"}" });
			this.#notify(ws, sessionId, {
				sessionUpdate: "tool_call_update",
				toolCallId: "slow-tool-1",
				status: "completed",
				content: "slow-output",
			});
			await sleep(1200);
			this.#notify(ws, sessionId, { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "slow after" } });
			return this.#result(ws, id, { stopReason: "stop" });
		}
		if (text.includes("permission")) {
			const outcome = await this.#requestPermission(ws, sessionId);
			this.permissionOutcome = outcome;
			if (text.includes("tool")) {
				this.#notify(ws, sessionId, { sessionUpdate: "tool_call", toolCallId: "perm-tool-1", title: "bash", rawInput: "{\"command\":\"echo permitted\"}" });
				this.#notify(ws, sessionId, {
					sessionUpdate: "tool_call_update",
					toolCallId: "perm-tool-1",
					status: "completed",
					content: "permitted-output",
				});
			}
			this.#notify(ws, sessionId, { sessionUpdate: "agent_message_chunk", content: { type: "text", text: `permission:selected:${outcome}` } });
			return this.#result(ws, id, { stopReason: "stop" });
		}
		if (text.includes("interleaved")) {
			this.#notify(ws, sessionId, { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "before tool" } });
			this.#notify(ws, sessionId, { sessionUpdate: "tool_call", toolCallId: "tool-interleaved", title: "scan", rawInput: "{\"pattern\":\"bar\"}" });
			this.#notify(ws, sessionId, {
				sessionUpdate: "tool_call_update",
				toolCallId: "tool-interleaved",
				status: "completed",
				content: "interleaved-result",
			});
			this.#notify(ws, sessionId, { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "after tool" } });
			return this.#result(ws, id, { stopReason: "stop" });
		}
		if (text.includes("tool")) {
			this.#notify(ws, sessionId, { sessionUpdate: "tool_call", toolCallId: "tool-1", title: "grep", rawInput: "{\"pattern\":\"foo\"}" });
			this.#notify(ws, sessionId, {
				sessionUpdate: "tool_call_update",
				toolCallId: "tool-1",
				status: "completed",
				content: Array.from({ length: 18 }, (_, index) => `tool-result-line-${index + 1}`).join("\n"),
			});
			this.#notify(ws, sessionId, { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "tool done" } });
			return this.#result(ws, id, { stopReason: "stop" });
		}
		this.#notify(ws, sessionId, { sessionUpdate: "agent_thought_chunk", content: { type: "text", text: "thinking" } });
		this.#notify(ws, sessionId, { sessionUpdate: "agent_message_chunk", content: { type: "text", text: `Echo: ${text}` } });
		return this.#result(ws, id, { stopReason: "stop" });
	}

	#requestPermission(ws: WebSocket, sessionId: string): Promise<string> {
		const id = ++this.#permissionRequestId;
		const promise = new Promise<string>(resolve => {
			this.#pendingServerRequests.set(id, (result: any) => {
				resolve(String(result?.outcome?.optionId ?? "missing"));
			});
		});
		ws.send(JSON.stringify({
			jsonrpc: "2.0",
			id,
			method: "session/request_permission",
			params: {
				sessionId,
				toolCall: { toolCallId: "perm-1", title: "Allow command?", inputSummary: "echo protected" },
				options: [
					{ optionId: "allow_once", name: "Allow once", kind: "allow_once" },
					{ optionId: "deny", name: "Deny", kind: "reject_once" },
				],
			},
		}));
		return promise;
	}

	#notify(ws: WebSocket, sessionId: string, update: Json): void {
		ws.send(JSON.stringify({ jsonrpc: "2.0", method: "session/update", params: { sessionId, update: { sessionId, ...update } } }));
	}

	#notifyExecution(ws: WebSocket, sessionId: string, execution: Json): void {
		ws.send(JSON.stringify({ jsonrpc: "2.0", method: methods.sessionExecutionUpdate, params: { sessionId, execution, _meta: { "mustang.agent/version": 1 } } }));
	}

	#result(ws: WebSocket, id: number, result: unknown): void {
		ws.send(JSON.stringify({ jsonrpc: "2.0", id, result }));
	}
}

await main();

async function main(): Promise<void> {
	const server = new FakeAcpKernel();
	await server.start();

	try {
		const command = [bunBin, "run", "src/cli/src/main.ts", "--new"];
		const result = await runPtyDriver(command, {
			KERNEL_URL: server.url,
			MUSTANG_TOKEN: token,
			TERM: "xterm-256color",
			COLUMNS: "100",
			LINES: "32",
		});

		assert(result.status === 0, `PTY probe failed with exit ${result.status}\n${result.output}`);
		for (const expected of [
			"Welcome back!",
			"Warning: No models available",
			"session",
			"List, resume, or delete sessions",
			"PTY_SHELL",
			"PTY_PY",
			"Resume Session",
			"Second session",
			"success grep",
			"tool-result-line-12",
			"Run /session delete confirm",
			"Deleted session and switched",
			"Allow command?",
			"permission:selected:allow_once",
		]) {
			assert(result.output.includes(expected), `PTY transcript should include ${JSON.stringify(expected)}\n${result.output}`);
		}

		for (const method of [
			"initialize",
			"session/new",
			methods.modelProfileList,
			methods.sessionExecuteShell,
			methods.sessionExecutePython,
			methods.sessionDelete,
			"session/prompt",
		]) {
			assert(server.calls.includes(method), `fake kernel should receive ${method}`);
		}
		assert(server.permissionOutcome === "allow_once", `permission overlay should return allow_once, got ${server.permissionOutcome}`);

		console.log("PASS: Phase B real CLI PTY/TUI probe");
	} finally {
		await server.stop();
	}
}

function promptText(prompt: unknown): string {
	if (!Array.isArray(prompt)) return "";
	return prompt.map(part => typeof part?.text === "string" ? part.text : "").join("");
}

function sleep(ms: number): Promise<void> {
	return new Promise(resolve => setTimeout(resolve, ms));
}

async function runPtyDriver(command: string[], env: Record<string, string>): Promise<{ status: number; output: string }> {
	const driver = String.raw`
import json, os, pty, re, select, signal, sys, time, termios, fcntl, struct

ansi_re = re.compile(r'(?:\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[PX^_][^\x1b]*(?:\x1b\\)|\x1b[@-Z\\-_])')
cmd = json.loads(os.environ["PTY_COMMAND_JSON"])
extra_env = json.loads(os.environ["PTY_EXTRA_ENV_JSON"])
env = os.environ.copy()
env.update(extra_env)

pid, fd = pty.fork()
if pid == 0:
    os.execvpe(cmd[0], cmd, env)

fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", int(env.get("LINES", "32")), int(env.get("COLUMNS", "100")), 0, 0))
os.set_blocking(fd, False)
raw = ""

def clean():
    return ansi_re.sub("", raw).replace("\r", "")

def read_for(seconds):
    global raw
    deadline = time.time() + seconds
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.05)
        if fd in r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                return
            if not data:
                return
            raw += data.decode("utf-8", "replace")

def send(data):
    os.write(fd, data.encode("utf-8"))
    read_for(0.15)

def expect(label, needles, timeout=8):
    if isinstance(needles, str):
        needles = [needles]
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = clean()
        if all(n in text for n in needles):
            print(f"PTY PASS: {label}", flush=True)
            return
        read_for(0.1)
    print(f"PTY FAIL: {label}; missing {needles}", flush=True)
    print(clean(), flush=True)
    cleanup(1)

def expect_order(label, before, after, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = clean()
        before_index = text.rfind(before)
        after_index = text.rfind(after)
        if before_index != -1 and after_index != -1 and before_index < after_index:
            print(f"PTY PASS: {label}", flush=True)
            return
        read_for(0.1)
    print(f"PTY FAIL: {label}; expected {before!r} before {after!r}", flush=True)
    print(clean(), flush=True)
    cleanup(1)

def expect_not_after(label, marker, forbidden, timeout=1):
    read_for(timeout)
    text = clean()
    marker_index = text.rfind(marker)
    if marker_index == -1:
        print(f"PTY FAIL: {label}; marker {marker!r} not found", flush=True)
        print(text, flush=True)
        cleanup(1)
    tail = text[marker_index:]
    if forbidden not in tail:
        print(f"PTY PASS: {label}", flush=True)
        return
    print(f"PTY FAIL: {label}; found {forbidden!r} after {marker!r}", flush=True)
    print(text, flush=True)
    cleanup(1)

def cleanup(code):
    try:
        os.write(fd, b"\x03\x03")
        time.sleep(0.2)
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass
    read_for(0.5)
    print("---- PTY TRANSCRIPT ----", flush=True)
    print(clean(), flush=True)
    sys.exit(code)

expect("first viewport", ["Welcome back!", "Warning: No models available", "no-model"])
send("/s")
expect("slash autocomplete", ["session", "List, resume, or delete sessions"])
send("\x1b")
send("\x03")
send("!echo PTY_SHELL\r")
expect("bang shell execution", ["$ echo PTY_SHELL", "PTY_SHELL"])
send('$print("PTY_PY")\r')
expect("dollar python execution", ["PTY_PY"])
send("/session list\r")
expect("session list renders via OMP selector", ["Resume Session", "Second session", "Enter to select"])
send("\r")
expect("session selector enter resumes session", ["Resumed session"])
send("\x1b")
read_for(0.2)
send("show tool\r")
expect("tool rendering collapsed", ["success grep", "tool-result-line-1", "Ctrl+O for more"])
expect_order("assistant answer follows tool output", "success grep", "tool done")
expect_not_after("completed tool is not rebuilt as pending after answer", "tool done", "pending grep")
expect_not_after("completed tool is not rebuilt after answer", "tool done", "success grep")
send("show interleaved tool\r")
expect("interleaved tool renders", ["before tool", "success scan", "interleaved-result", "after tool"])
expect_order("interleaved intro precedes tool", "before tool", "success scan")
expect_order("interleaved answer follows tool", "success scan", "after tool")
send("show slow stream\r")
expect("slow stream text renders before turn ends", ["slow before"], 0.7)
expect("slow stream tool renders before turn ends", ["success slowbash", "slow-output"], 1.3)
expect("slow stream final text renders", ["slow after"], 3)
send("\x0f")
expect("ctrl-o expands tool output", ["success grep", "tool-result-line-12"])
send("/session delete")
read_for(0.3)
send("\x1b")
send("\r")
expect("delete requires confirm", ["Run /session delete confirm"])
send("/session delete confirm")
read_for(0.3)
send("\x1b")
send("\r")
expect("delete confirm calls ACP", ["Deleted session and switched"])
send("ask permission\r")
expect("permission overlay", ["Allow command?", "Allow once"])
send("\r")
expect("permission response returns to CLI", ["permission:selected:allow_once"])
send("ask permission tool\r")
expect("permission tool overlay", ["Allow command?", "Allow once"])
send("\r")
expect("permission-gated tool result streams before final text", ["success bash", "permitted-output", "permission:selected:allow_once"])
expect_order("permission-gated tool precedes final text", "success bash", "permission:selected:allow_once")
cleanup(0)
`;

	return await new Promise(resolve => {
		const child = spawn("python3", ["-c", driver], {
			cwd: process.cwd(),
			env: {
				...process.env,
				PTY_COMMAND_JSON: JSON.stringify(command),
				PTY_EXTRA_ENV_JSON: JSON.stringify(env),
			},
			stdio: ["ignore", "pipe", "pipe"],
		});
		let output = "";
		child.stdout.on("data", chunk => output += chunk.toString());
		child.stderr.on("data", chunk => output += chunk.toString());
		child.on("close", status => resolve({ status: status ?? 1, output }));
		setTimeout(() => {
			child.kill("SIGTERM");
			resolve({ status: 124, output: `${output}\nPTY driver timed out` });
		}, 45_000).unref();
	});
}
