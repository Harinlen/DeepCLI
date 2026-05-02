import { highlightCode, supportsLanguage, type HighlightColors } from "../src/compat/natives.js";
import { assert } from "./helpers.js";

const colors: HighlightColors = {
	comment: "<C>",
	keyword: "<K>",
	function: "<F>",
	variable: "<V>",
	string: "<S>",
	number: "<N>",
	type: "<T>",
	operator: "<O>",
	punctuation: "<P>",
	inserted: "<I>",
	deleted: "<D>",
};

const reset = "\x1b[0m";

assert(supportsLanguage("cpp"), "fallback highlighter should recognize cpp");
assert(supportsLanguage("C++"), "fallback highlighter should recognize c++ alias");
assert(supportsLanguage("python"), "fallback highlighter should recognize python");
assert(supportsLanguage("ts"), "fallback highlighter should recognize TypeScript alias");
assert(!supportsLanguage("definitely-not-a-language"), "fallback highlighter should reject unknown languages");

const cpp = highlightCode("int main() {\n  // done\n  return 0;\n}", "cpp", colors);
assert(cpp.includes(`<T>int${reset}`), "cpp highlighter should color primitive types");
assert(cpp.includes(`<F>main${reset}`), "cpp highlighter should color call-like identifiers as functions");
assert(cpp.includes(`<C>// done${reset}`), "cpp highlighter should color comments");
assert(cpp.includes(`<K>return${reset}`), "cpp highlighter should color keywords");
assert(cpp.includes(`<N>0${reset}`), "cpp highlighter should color numbers");

const python = highlightCode("def solve():\n    return 7", "python", colors);
assert(python.includes(`<K>def${reset}`), "python highlighter should color keywords");
assert(python.includes(`<F>solve${reset}`), "python highlighter should color functions");

console.log("PASS: syntax highlight fallback");
