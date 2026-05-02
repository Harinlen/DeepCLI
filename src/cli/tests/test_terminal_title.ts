import { formatSessionTerminalTitle } from "../src/terminal-title.js";
import { assert } from "./helpers.js";

assert(formatSessionTerminalTitle(undefined, "/repo/cli", undefined) === "DeepCLI", "default title should be DeepCLI");
assert(formatSessionTerminalTitle("", "/repo/cli", undefined) === "DeepCLI", "empty title should fall back to DeepCLI");
assert(
	formatSessionTerminalTitle("Planning Session", "/repo/cli", "auto") === "Planning Session",
	"session title should replace the default title",
);
assert(
	formatSessionTerminalTitle("  \u0000Renamed\u0007  ", "/repo/cli", "user") === "Renamed",
	"session title should be sanitized before display",
);

console.log("PASS: terminal title");
