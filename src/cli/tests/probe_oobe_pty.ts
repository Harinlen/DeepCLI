import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { WebSocketServer, WebSocket } from "ws";
import { assert } from "./helpers.js";

type Json = Record<string, any>;

const bunBin = process.env.BUN_BIN ?? Bun.which("bun") ?? `${process.env.HOME}/.bun/bin/bun`;
const methods = {
	commandsList: "_mustang.agent/commands/list",
	modelProfileList: "_mustang.agent/model/profile_list",
	modelProviderList: "_mustang.agent/model/provider_list",
	modelSetCurrent: "_mustang.agent/model/set_current",
	modelAdd: "_mustang.agent/model/add",
	modelUpdate: "_mustang.agent/model/update",
} as const;

class FakeOobeKernel {
	#server?: WebSocketServer;
	#sessionCounter = 0;
	url = "";
	calls: string[] = [];
	addedModels: string[] = [];
	currentUsed: Record<string, [string, string]> = {};

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
			const params = message.params as Json ?? {};
			this.calls.push(method);
			switch (method) {
				case "initialize":
					return this.#result(ws, id, { protocolVersion: 1, serverInfo: { name: "fake-oobe", version: "0" } });
				case "session/new":
					this.#sessionCounter += 1;
					return this.#result(ws, id, { sessionId: `oobe-session-${this.#sessionCounter}`, configOptions: [], modes: [{ id: "default", name: "Default" }] });
				case "session/prompt":
					this.#notify(ws, String(params.sessionId ?? `oobe-session-${this.#sessionCounter}`), { sessionUpdate: "agent_message_chunk", content: { type: "text", text: `Echo: ${promptText(params.prompt)}` } });
					return this.#result(ws, id, { stopReason: "stop" });
				case methods.commandsList:
					return this.#result(ws, id, { commands: [] });
				case methods.modelProfileList:
					return this.#result(ws, id, { profiles: [], defaultModel: "" });
				case methods.modelProviderList:
					return this.#result(ws, id, this.#providerList());
				case methods.modelAdd:
					this.addedModels.push(String(params.modelId));
					return this.#result(ws, id, {
						model: ["deepseek", String(params.modelId)],
						providerType: "deepseek",
						effectiveBaseUrl: "https://api.deepseek.com",
						hasApiKey: true,
						apiKeyDisplay: String(params.apiKey ?? ""),
						settingFields: ["api_key", "base_url"],
						displayName: params.displayName,
						contextWindow: params.contextWindow,
						roles: params.roles ?? [],
					});
				case methods.modelUpdate:
					return this.#result(ws, id, {
						model: ["deepseek", String(params.model ?? params.modelId)],
						providerType: "deepseek",
						effectiveBaseUrl: "https://api.deepseek.com",
						hasApiKey: true,
						apiKeyDisplay: String(params.apiKey ?? ""),
						settingFields: ["api_key", "base_url"],
						displayName: params.displayName,
						contextWindow: params.contextWindow,
						roles: params.roles ?? [],
					});
				case methods.modelSetCurrent:
					this.currentUsed[String(params.role)] = [String(params.provider), String(params.model)];
					return this.#result(ws, id, { role: String(params.role), model: [String(params.provider), String(params.model)] });
				default:
					return this.#result(ws, id, {});
			}
		});
	}

	#providerList(): Json {
		return {
			defaultContextWindow: 128_000,
			providerTypeOptions: [
				{ providerType: "deepseek", settingFields: ["api_key", "base_url"], effectiveBaseUrl: "https://api.deepseek.com" },
			],
			currentUsed: this.currentUsed,
			providers: this.addedModels.length
				? [{
					name: "deepseek",
					providerType: "deepseek",
					hasApiKey: true,
					apiKeyDisplay: "sk-oobe-test",
					hasAwsSecretKey: false,
					effectiveBaseUrl: "https://api.deepseek.com",
					settingFields: ["api_key", "base_url"],
					models: this.addedModels,
					contextWindows: Object.fromEntries(this.addedModels.map(model => [model, 1_000_000])),
					displayNames: {
						"deepseek-v4-pro": "DeepSeek V4 Pro",
						"deepseek-v4-flash": "DeepSeek V4 Flash",
					},
					roles: {},
				}]
				: [],
		};
	}

	#result(ws: WebSocket, id: number, result: unknown): void {
		ws.send(JSON.stringify({ jsonrpc: "2.0", id, result }));
	}

	#notify(ws: WebSocket, sessionId: string, update: Json): void {
		ws.send(JSON.stringify({ jsonrpc: "2.0", method: "session/update", params: { sessionId, update: { sessionId, ...update } } }));
	}
}

function promptText(prompt: unknown): string {
	if (!Array.isArray(prompt)) return "";
	return prompt.map(part => typeof part?.text === "string" ? part.text : "").join("");
}

await main();

async function main(): Promise<void> {
	const server = new FakeOobeKernel();
	const configDir = mkdtempSync(join(tmpdir(), "deepcli-oobe-pty-config-"));
	await server.start();
	try {
		const ctrlCConfigDir = mkdtempSync(join(tmpdir(), "deepcli-oobe-ctrlc-config-"));
		const ctrlCResult = await runPtyDriver([bunBin, "run", "src/main.ts", "--new"], {
			KERNEL_URL: server.url,
			DEEPCLI_TOKEN: "test-token",
			TERM: "xterm-256color",
			COLUMNS: "100",
			LINES: "32",
			DEEPCLI_CONFIG_DIR: ctrlCConfigDir,
			PTY_SCENARIO: "ctrlc",
		});
		rmSync(ctrlCConfigDir, { recursive: true, force: true });
		assert(ctrlCResult.status === 0, `OOBE Ctrl+C PTY probe failed with exit ${ctrlCResult.status}\n${ctrlCResult.output}`);

		const skipConfigDir = mkdtempSync(join(tmpdir(), "deepcli-oobe-skip-config-"));
		const skipResult = await runPtyDriver([bunBin, "run", "src/main.ts", "--new"], {
			KERNEL_URL: server.url,
			DEEPCLI_TOKEN: "test-token",
			TERM: "xterm-256color",
			COLUMNS: "100",
			LINES: "32",
			DEEPCLI_CONFIG_DIR: skipConfigDir,
			PTY_SCENARIO: "skip",
		});
		rmSync(skipConfigDir, { recursive: true, force: true });
		assert(skipResult.status === 0, `OOBE Skip PTY probe failed with exit ${skipResult.status}\n${skipResult.output}`);

		const result = await runPtyDriver([bunBin, "run", "src/main.ts", "--new"], {
			KERNEL_URL: server.url,
			DEEPCLI_TOKEN: "test-token",
			TERM: "xterm-256color",
			COLUMNS: "100",
			LINES: "32",
			DEEPCLI_CONFIG_DIR: configDir,
		});
		assert(result.status === 0, `OOBE PTY probe failed with exit ${result.status}\n${result.output}`);
		assert(server.addedModels.includes("deepseek-v4-pro"), "OOBE should add DeepSeek V4 Pro");
		assert(server.addedModels.includes("deepseek-v4-flash"), "OOBE should add DeepSeek V4 Flash");
		assert(server.currentUsed.default?.[1] === "deepseek-v4-pro", "OOBE should set V4 Pro as default");
		assert(server.currentUsed.compact?.[1] === "deepseek-v4-flash", "OOBE should set V4 Flash as compact");
		console.log("PASS: OOBE real CLI PTY probe");
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
scenario = env.get("PTY_SCENARIO", "configure")

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

def expect_not(label, needles, timeout=1):
    if isinstance(needles, str):
        needles = [needles]
    read_for(timeout)
    text = clean()
    found = [n for n in needles if n in text]
    if not found:
        print(f"PTY PASS: {label}", flush=True)
        return
    print(f"PTY FAIL: {label}; found {found}", flush=True)
    print(text, flush=True)
    cleanup(1)

def expect_tail(label, needles, forbidden=None, timeout=1, tail_chars=6000):
    if isinstance(needles, str):
        needles = [needles]
    forbidden = [] if forbidden is None else ([forbidden] if isinstance(forbidden, str) else forbidden)
    read_for(timeout)
    tail = clean()[-tail_chars:]
    missing = [n for n in needles if n not in tail]
    found = [n for n in forbidden if n in tail]
    if not missing and not found:
        print(f"PTY PASS: {label}", flush=True)
        return
    print(f"PTY FAIL: {label}; missing {missing}; found forbidden {found}", flush=True)
    print(tail, flush=True)
    cleanup(1)

def expect_tail_order(label, before, after, timeout=1, tail_chars=6000):
    read_for(timeout)
    text = clean()[-tail_chars:]
    before_index = text.rfind(before)
    after_index = text.rfind(after)
    if before_index >= 0 and after_index >= 0 and before_index < after_index:
        print(f"PTY PASS: {label}", flush=True)
        return
    print(f"PTY FAIL: {label}; expected {before!r} before {after!r}", flush=True)
    print(text, flush=True)
    cleanup(1)

def expect_exit(label, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        read_for(0.1)
        result = os.waitpid(pid, os.WNOHANG)
        if result[0] == pid:
            status = result[1]
            if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
                print(f"PTY PASS: {label}", flush=True)
                sys.exit(0)
            print(f"PTY FAIL: {label}; child status {status}", flush=True)
            print(clean(), flush=True)
            sys.exit(1)
    print(f"PTY FAIL: {label}; process did not exit", flush=True)
    print(clean(), flush=True)
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

expect("welcome", ["Welcome to DeepCLI", "Set up DeepSeek", "Set up others", "Skip to main window"])
expect_not("oobe is first screen", ["Welcome back!", "Warning: No models available."])
if scenario == "ctrlc":
    send("\x03")
    expect_exit("ctrl+c exits startup OOBE")
if scenario == "skip":
    send("\x1b[B")
    send("\x1b[B")
    send("\r")
    expect_tail("skip returns to visible welcome", ["Welcome back!", "Tips", "Recent sessions"])
    cleanup(0)
send("\r")
expect("deepseek setup", ["Get a DeepSeek API key", "https://platform.deepseek.com/api_keys", "DeepSeek V4 Pro <1M>", "DeepSeek V4 Flash <1M>"])
send("\x1b[200~sk-oobe-test\x1b[201~")
send("\r")
expect("configured", ["Model configured. You can change it later with /model.", "Welcome back!", "DeepSeek V4 Pro"])
expect_not("configured screen has selected model", "no-model")
expect_tail("configured screen remains visible", ["Model configured. You can change it later with /model.", "Welcome back!", "DeepSeek V4 Pro"], "no-model")
send("oobe-layout-check\r")
expect("post-oobe prompt", ["Echo: oobe-layout-check"])
expect_tail_order("post-oobe welcome stays above transcript", "Welcome back!", "oobe-layout-check")
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
