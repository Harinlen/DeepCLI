import { ArgError, parseCliArgs, usage } from "../src/startup/args.js";
import { assert } from "./helpers.js";

let args = parseCliArgs(["--resume", "session-123"]);
assert(args.sessionId === "session-123", "--resume should load a session id");

args = parseCliArgs(["--session", "session-456"]);
assert(args.sessionId === "session-456", "--session should still load a session id");

try {
	parseCliArgs(["--resume"]);
	assert(false, "--resume without a value should fail");
} catch (error) {
	assert(error instanceof ArgError, "--resume without a value should throw ArgError");
	assert((error as Error).message === "--resume requires a value", "--resume missing value error should name --resume");
}

assert(usage().includes("--resume <id>"), "usage should document --resume");

console.log("PASS: startup args");
