import { formatWebFetchSetupFailure } from "../src/webfetch/diagnostics.js";
import { assert } from "./helpers.js";

const message = formatWebFetchSetupFailure("Crawl4AI setup failed.", {
	ok: false,
	logs: [
		{
			command: "uv pip install --python /deepcli/python 'crawl4ai>=0.6.3'",
			exitCode: 1,
			stderr: "No matching distribution found for crawl4ai",
		},
	],
});

assert(message.includes("Crawl4AI setup failed."), "diagnostic should keep the high-level message");
assert(message.includes("Command: uv pip install --python /deepcli/python"), "diagnostic should include failed command");
assert(message.includes("Exit code: 1"), "diagnostic should include exit code");
assert(message.includes("No matching distribution found"), "diagnostic should include stderr");

console.log("PASS: webfetch diagnostics");
