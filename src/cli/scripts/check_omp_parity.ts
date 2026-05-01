import { readFileSync } from "node:fs";
import { resolve } from "node:path";

interface PortEntry {
	upstream: string;
	target: string;
}

interface Manifest {
	upstreamRoot: string;
	ports: PortEntry[];
}

const cliRoot = resolve(import.meta.dir, "..");
const repoRoot = resolve(cliRoot, "..", "..");
const manifest = JSON.parse(readFileSync(resolve(cliRoot, "active-port-manifest.json"), "utf8")) as Manifest;
const upstreamRoot = resolve(manifest.upstreamRoot);

const upstreamIdenticalExceptTsNocheck = new Set([
	"packages/coding-agent/src/modes/interactive-mode.ts",
	"packages/coding-agent/src/modes/controllers/input-controller.ts",
	"packages/coding-agent/src/modes/controllers/command-controller.ts",
	"packages/coding-agent/src/modes/components/assistant-message.ts",
	"packages/coding-agent/src/modes/components/tool-execution.ts",
	"packages/coding-agent/src/modes/components/hook-selector.ts",
	"packages/coding-agent/src/modes/components/hook-input.ts",
	"packages/coding-agent/src/modes/components/status-line.ts",
	"packages/coding-agent/src/modes/components/session-selector.ts",
]);

const documentedAdapterSeams = new Map([
	[
		"packages/coding-agent/src/modes/controllers/event-controller.ts",
		"DeepCLI adapter can emit tool-first turns before visible assistant text; local lazy-mount guard preserves OMP component ordering.",
	],
	[
		"packages/coding-agent/src/modes/controllers/selector-controller.ts",
		"Session deletion is routed through ACP when available instead of OMP FileSessionStorage side effects.",
	],
	[
		"packages/coding-agent/src/modes/controllers/extension-ui-controller.ts",
		"Extension runner services are not backed by DeepCLI ACP yet; production permission prompts use the OMP hook dialog host subset.",
	],
	[
		"packages/coding-agent/src/slash-commands/builtin-registry.ts",
		"Builtin dispatch is ACP-backed and hides/degrades unsupported OMP services while preserving OMP UI entry points.",
	],
	[
		"packages/coding-agent/src/session/agent-session.ts",
		"DeepCLI owns the ACP-backed AgentSession adapter contract; OMP's local agent loop is intentionally not ported.",
	],
	[
		"packages/coding-agent/src/session/session-manager.ts",
		"DeepCLI supplies OMP SessionInfo rows from ACP instead of reading local JSONL session files.",
	],
]);

function normalizeCopiedSource(source: string): string {
	return source
		.replace(/^\/\/\s*@?ts-nocheck\s*\n/, "")
		.replace(/^\/\/\s*-nocheck\s*\n/, "")
		.replace(/\r\n/g, "\n");
}

function portTarget(upstream: string): string {
	const entry = manifest.ports.find(item => item.upstream === upstream);
	if (!entry) throw new Error(`No active-port manifest entry for ${upstream}`);
	return resolve(repoRoot, entry.target);
}

function fail(message: string): never {
	console.error(`FAIL: ${message}`);
	process.exit(1);
}

let checkedStrict = 0;
for (const upstream of upstreamIdenticalExceptTsNocheck) {
	const upstreamSource = normalizeCopiedSource(readFileSync(resolve(upstreamRoot, upstream), "utf8"));
	const localSource = normalizeCopiedSource(readFileSync(portTarget(upstream), "utf8"));
	if (upstreamSource !== localSource) {
		fail(`${upstream} must match OMP baseline except for a leading ts-nocheck marker`);
	}
	checkedStrict++;
}

let checkedSeams = 0;
for (const upstream of documentedAdapterSeams.keys()) {
	portTarget(upstream);
	checkedSeams++;
}

console.log(`PASS: OMP parity (${checkedStrict} strict files, ${checkedSeams} documented adapter seams)`);
