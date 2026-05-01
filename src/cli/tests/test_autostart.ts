import { maybeAutostartKernel, isLoopbackWsUrl } from "../src/startup/autostart.js";
import { DEFAULT_CONFIG } from "../src/config/schema.js";
import { assert } from "./helpers.js";

assert(isLoopbackWsUrl("ws://localhost:8200"), "localhost should be loopback");
assert(isLoopbackWsUrl("ws://127.0.0.1:8200"), "127.0.0.1 should be loopback");
assert(!isLoopbackWsUrl("ws://example.com:8200"), "remote host should not be loopback");

let attempts = 0;
let killed: boolean = false;
const config = {
  ...DEFAULT_CONFIG,
  kernel: {
    ...DEFAULT_CONFIG.kernel,
    autostart: true,
    autostart_command: "bun run kernel",
  },
};

const handle = await maybeAutostartKernel(config, {
  connect: async () => {
    attempts++;
    if (attempts < 2) throw new Error("not ready");
    return { close() {} };
  },
  spawnProcess: () => ({ killed: false, kill: () => { killed = true; } }) as any,
  waitMs: 1_000,
});
assert(attempts === 2, "autostart should retry readiness");
handle.stop();
assert(killed, "autostart handle should stop spawned process");

console.log("PASS: kernel autostart guards and readiness");
