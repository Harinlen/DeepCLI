import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const bunBin = process.env.BUN_BIN ?? Bun.which("bun") ?? `${process.env.HOME}/.bun/bin/bun`;

const tests = [
	"test_autocomplete_sort.ts",
	"test_agent_session_adapter.ts",
	"test_input_controller_r4.ts",
	"test_session_selector_omp.ts",
	"test_status_line.ts",
	"test_ui_golden_r5.ts",
];

let passed = 0;
let failed = 0;

for (const test of tests) {
	const result = spawnSync(bunBin, ["run", join(__dirname, test)], {
		stdio: "inherit",
		env: process.env,
	});
	if (result.status === 0) {
		passed++;
		continue;
	}
	failed++;
	console.error(`\nFAILED: ${test} (exit ${result.status})`);
	break;
}

console.log("\nPhase B UI parity coverage summary");
console.log("covered: R1 upstream status line import, R2 ACP event adapter, R3 copied InteractiveMode/InputController/Command/Event/Selector main path");
console.log("covered: slash command names, /session args, /model args, /theme args, prompt-action autocomplete");
console.log("covered: R4 !/!!/$/$$ routing, mode border transitions, Escape cancel paths, Ctrl+C semantics, /session delete confirm guard");
console.log("covered: R5 golden frames for welcome, editor/status, autocomplete, no-model warning, assistant/thinking, tool states, permission overlay");
console.log("covered: OMP-first session selector component and ACP-backed selector rows");
console.log("covered separately: R6 real CLI PTY/TUI E2E via probe_phase_b_pty.ts");
console.log("allowlisted: startup selector remains Phase D readline fallback, outside main TUI Phase B gate");
console.log("allowlisted: heavy OMP selector sub-UIs are stubbed in DeepCLI ACP mode until their backing services exist");
console.log("missing: none for R1-R6 main Phase B repair gates; deeper service-heavy selector sub-UIs remain allowlisted");

console.log("\n─────────────────────────────────");
console.log(`Phase B results: ${passed} passed, ${failed} failed`);
console.log("─────────────────────────────────");

process.exit(failed > 0 ? 1 : 0);
