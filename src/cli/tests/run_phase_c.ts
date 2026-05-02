import { spawnSync } from "child_process";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const bunBin = process.env.BUN_BIN ?? Bun.which("bun") ?? `${process.env.HOME}/.bun/bin/bun`;

const tests = [
  "test_permission_mapper.ts",
  "test_permission_queue.ts",
  "test_permission_host.ts",
  "test_ask_user_question_permission.ts",
  "test_permission_controller_import.ts",
  "test_acp_permission_response.ts",
  "test_acp_disconnect.ts",
  "test_acp_reconnect.ts",
  "test_acp_client_turn_id.ts",
  "test_session_resume_before_prompt.ts",
  "test_session_resume_retries_transient_internal.ts",
  "test_session_prompt_retries_client_turn.ts",
  "test_permission_auto_mode.ts",
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

console.log("\n─────────────────────────────────");
console.log(`Phase C results: ${passed} passed, ${failed} failed`);
console.log("─────────────────────────────────");

process.exit(failed > 0 ? 1 : 0);
