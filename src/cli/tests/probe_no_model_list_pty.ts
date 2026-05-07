import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { WebSocketServer, WebSocket } from "ws";
import { assert } from "./helpers.js";

type Json = Record<string, any>;

const bunBin = process.env.BUN_BIN ?? Bun.which("bun") ?? `${process.env.HOME}/.bun/bin/bun`;

class FakeNoModelKernel {
	#server?: WebSocketServer;
	url = "";

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
			const message = JSON.parse(raw.toString()) as Json;
			const id = Number(message.id);
			const method = String(message.method ?? "");
			switch (method) {
				case "initialize":
					return this.#result(ws, id, { protocolVersion: 1, serverInfo: { name: "fake-no-model", version: "0" } });
				case "session/new":
					return this.#result(ws, id, { sessionId: "no-model-session", configOptions: [], modes: [{ id: "default", name: "Default" }] });
				case "session/list":
					return this.#result(ws, id, { sessions: [] });
				case "_mustang.agent/commands/list":
					return this.#result(ws, id, { commands: [] });
				case "_mustang.agent/model/profile_list":
					return this.#result(ws, id, { profiles: [], defaultModel: "" });
				case "_mustang.agent/model/provider_list":
					return this.#result(ws, id, {
						defaultContextWindow: 128_000,
						providerTypeOptions: [{ providerType: "deepseek", settingFields: ["api_key", "base_url"], effectiveBaseUrl: "https://api.deepseek.com" }],
						currentUsed: {},
						providers: [],
					});
				default:
					return this.#result(ws, id, {});
			}
		});
	}

	#result(ws: WebSocket, id: number, result: unknown): void {
		ws.send(JSON.stringify({ jsonrpc: "2.0", id, result }));
	}
}

await main();

async function main(): Promise<void> {
	const server = new FakeNoModelKernel();
	const configDir = mkdtempSync(join(tmpdir(), "deepcli-no-model-list-"));
	mkdirSync(configDir, { recursive: true });
	writeFileSync(join(configDir, "client.yaml"), [
		"oobe:",
		"  revision: 1",
		"  status: skipped",
		"  checked_at: null",
		"  skipped_at: test",
		"",
	].join("\n"));
	await server.start();
	try {
		const result = await runPtyDriver([bunBin, "run", "src/main.ts", "--new"], {
			KERNEL_URL: server.url,
			DEEPCLI_TOKEN: "test-token",
			TERM: "xterm-256color",
			COLUMNS: "100",
			LINES: "32",
			DEEPCLI_CONFIG_DIR: configDir,
		});
		assert(result.status === 0, `No-model /model list PTY probe failed with exit ${result.status}\n${result.output}`);
		console.log("PASS: no-model /model list real CLI PTY probe");
	} finally {
		await server.stop();
		rmSync(configDir, { recursive: true, force: true });
	}
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
    read_for(0.2)

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

def expect_not(label, needle, timeout=1):
    read_for(timeout)
    text = clean()
    if needle not in text:
        print(f"PTY PASS: {label}", flush=True)
        return
    print(f"PTY FAIL: {label}; found {needle!r}", flush=True)
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

expect("main screen", "Warning: No models available. Use /model add to add a model.")
send("\x1b[200~/model list\x1b[201~")
send("\x1b")
send("\r")
expect("direct warning", "No models available. Use /model add to add a model.")
expect_not("does not open selector", "Current-used roles are shown at the right of each model.")
send("\x03")
send("\x1b[200~/model add\x1b[201~")
send("\x1b")
send("\r")
expect("model add offers no-provider setup choices", ["Set up a model", "No providers are configured yet.", "Set up DeepSeek", "Set up others", "Cancel"])
expect_not("model add does not show startup welcome copy", "Welcome to DeepCLI")
expect_not("no fake existing provider choice", "Existing provider")
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
		}, 25_000).unref();
	});
}
