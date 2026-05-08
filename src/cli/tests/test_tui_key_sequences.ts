import assert from "assert";

import { matchesKey, parseKey, type KeyId } from "../src/active-port/tui/keys.js";

const cases: Array<[string, string]> = [
	["\x1b[H", "home"],
	["\x1bOH", "home"],
	["\x1b[1~", "home"],
	["\x1b[7~", "home"],
	["\x1b[F", "end"],
	["\x1bOF", "end"],
	["\x1b[4~", "end"],
	["\x1b[8~", "end"],
	["\x1b[3~", "delete"],
];

for (const [sequence, key] of cases) {
	assert.equal(parseKey(sequence), key, `${JSON.stringify(sequence)} should parse as ${key}`);
	assert(matchesKey(sequence, key as KeyId), `${JSON.stringify(sequence)} should match ${key}`);
}

console.log("PASS: TUI parses common Home/End/Delete key sequences");
