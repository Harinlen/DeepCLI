/**
 * Run all Phase A tests in sequence.
 * Exit 0 if all pass, exit 1 on first failure.
 *
 * Usage:
 *   KERNEL_URL=ws://localhost:8200 MUSTANG_TOKEN=dev bun run tests/run_all.ts
 *   KERNEL_PORT=8200 MUSTANG_TOKEN=dev bun run tests/run_all.ts
 */

import { spawnSync } from "child_process";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));

const tests = [
  "test_connect.ts",
  "test_session.ts",
  "test_prompt.ts",
  "test_multiturn.ts",
];

let passed = 0;
let failed = 0;

for (const test of tests) {
  const path = join(__dirname, test);
  const bunBin = process.env.BUN_BIN ?? Bun.which("bun") ?? `${process.env.HOME}/.bun/bin/bun`;
  const result = spawnSync(bunBin, ["run", path], {
    stdio: "inherit",
    env: process.env,
  });

  if (result.status === 0) {
    passed++;
  } else {
    failed++;
    console.error(`\nFAILED: ${test} (exit ${result.status})`);
    break; // stop on first failure
  }
}

console.log(`\n─────────────────────────────────`);
console.log(`Results: ${passed} passed, ${failed} failed`);
console.log(`─────────────────────────────────`);

process.exit(failed > 0 ? 1 : 0);
