import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createServer } from "node:net";
import { AcpClient } from "../src/acp/client.js";
import { assert } from "./helpers.js";

const repoRoot = resolve(import.meta.dir, "../../..");
const runKernel = join(repoRoot, "scripts", "run-kernel.sh");

const tempRoot = mkdtempSync(join(tmpdir(), "deepcli-token-rotation-"));
const stateDir = join(tempRoot, "state");
const port = await freePort();
const url = `ws://127.0.0.1:${port}`;

let kernel: ChildProcess | undefined;
let client: AcpClient | undefined;
let passed = false;

try {
	kernel = startKernel();
	await waitReady(port);
	const firstToken = readToken();
	client = await AcpClient.connect(url, firstToken, { tokenProvider: readToken });
	const first = await client.request<{ sessions?: unknown[] }>("session/list", {}, { timeoutMs: 10_000 });
	assert(Array.isArray(first.sessions), "initial real kernel session/list should work");

	await stopKernel(kernel);
	kernel = undefined;
	await waitStopped(port);

	kernel = startKernel();
	await waitReady(port);
	const secondToken = readToken();
	assert(secondToken !== firstToken, "restarted real kernel should rotate auth token");

	const second = await client.request<{ sessions?: unknown[] }>("session/list", {}, { timeoutMs: 20_000 });
	assert(Array.isArray(second.sessions), "existing ACP client should reconnect with rotated token");

	console.log("probe=kernel_token_rotation_reconnect");
	console.log("token_rotated=true");
	console.log("reconnected_with_rotated_token=true");
	console.log("result=PASS");
	passed = true;
} finally {
	client?.close();
	if (kernel) await stopKernel(kernel).catch(() => {});
	rmSync(tempRoot, { recursive: true, force: true });
}

if (passed) process.exit(0);

function startKernel(): ChildProcess {
	return spawn(runKernel, ["--access-port", String(port), "--state-dir", stateDir, "--dev"], {
		cwd: repoRoot,
		env: {
			...process.env,
			DEEPCLI_STATE_DIR: stateDir,
			DEEPCLI_HOME: tempRoot,
		},
		stdio: ["ignore", "ignore", "ignore"],
	});
}

async function stopKernel(child: ChildProcess): Promise<void> {
	if (child.exitCode !== null || child.killed) return;
	child.kill("SIGINT");
	await new Promise<void>((resolve) => {
		const timer = setTimeout(() => {
			child.kill("SIGKILL");
			resolve();
		}, 5_000);
		child.once("close", () => {
			clearTimeout(timer);
			resolve();
		});
	});
}

function readToken(): string {
	return readFileSync(join(stateDir, "auth_token"), "utf8").trim();
}

async function waitReady(port: number): Promise<void> {
	const deadline = Date.now() + 45_000;
	while (Date.now() < deadline) {
		try {
			const response = await fetch(`http://127.0.0.1:${port}/access/readiness`);
			if (response.ok) {
				const body = await response.json() as { default_route_ready?: boolean };
				if (body.default_route_ready === true) return;
			}
		} catch {
			// Keep polling until the supervised Access Router is listening.
		}
		await sleep(250);
	}
	throw new Error(`Kernel on port ${port} did not become ready`);
}

async function waitStopped(port: number): Promise<void> {
	const deadline = Date.now() + 10_000;
	while (Date.now() < deadline) {
		try {
			await fetch(`http://127.0.0.1:${port}/access/readiness`);
		} catch {
			return;
		}
		await sleep(100);
	}
}

async function freePort(): Promise<number> {
	const server = createServer();
	await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
	const address = server.address();
	await new Promise<void>((resolve) => server.close(() => resolve()));
	if (!address || typeof address === "string") throw new Error("Could not allocate free port");
	return address.port;
}

function sleep(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}
