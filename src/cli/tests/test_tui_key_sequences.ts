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
	["\x1b[Z", "shift+tab"],
	["\x1b[9;2u", "shift+tab"],
	["\x1bp", "alt+p"],
	["\x1bP", "shift+alt+p"],
	["\x1bh", "alt+h"],
	["\x1b[1;3A", "alt+up"],
	["\x1b[1;5D", "ctrl+left"],
	["\x1b[1;5C", "ctrl+right"],
	["\x1b[13;5u", "ctrl+enter"],
	["\x1b[80;6u", "ctrl+shift+p"],
	["\x1b[27;1;27~", "escape"],
	["\x1b[27;1;27u", "escape"],
	["\x1b[27;3;112~", "alt+p"],
	["\x1b[27;4;80~", "shift+alt+p"],
	["\x1b[27;5;111~", "ctrl+o"],
	["\x1b[27;6;80~", "ctrl+shift+p"],
];

for (const [sequence, key] of cases) {
	assert.equal(parseKey(sequence), key, `${JSON.stringify(sequence)} should parse as ${key}`);
	assert(matchesKey(sequence, key as KeyId), `${JSON.stringify(sequence)} should match ${key}`);
}

assert(matchesKey("\x1bP", "alt+shift+p"), "legacy Alt+Shift+P should match configured alt+shift+p");
assert(matchesKey("\x1b[80;6u", "shift+ctrl+p"), "CSI-u Shift+Ctrl+P should match configured shift+ctrl+p");

console.log("PASS: TUI parses common Home/End/Delete key sequences");
