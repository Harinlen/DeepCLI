import { Markdown, type MarkdownTheme } from "../src/active-port/tui/components/markdown.js";
import { assert } from "./helpers.js";

const theme: MarkdownTheme = {
	heading: text => text,
	link: text => text,
	linkUrl: text => text,
	code: text => `<CODE>${text}</CODE>`,
	codeBlock: text => text,
	codeBlockBorder: text => text,
	quote: text => text,
	quoteBorder: text => text,
	hr: text => text,
	listBullet: text => text,
	bold: text => text,
	italic: text => text,
	strikethrough: text => text,
	underline: text => text,
	symbols: {
		quoteBorder: ">",
		hrChar: "-",
		spinnerFrames: ["-"],
		boxRound: {} as never,
		boxSharp: {} as never,
		table: {} as never,
		cursor: ">",
		inputCursor: "|",
	},
};

const normal = new Markdown("plain `code`", 0, 0, theme).render(80).join("\n");
assert(normal.includes("<CODE>code</CODE>"), "normal inline code should use markdown code style");

const thinking = new Markdown("plain `code`", 0, 0, theme, {
	color: text => `<THINK>${text}</THINK>`,
	codeColor: text => `<THINKCODE>${text}</THINKCODE>`,
}).render(80).join("\n");
assert(thinking.includes("<THINK>plain </THINK>"), "thinking text should use default color");
assert(thinking.includes("<THINKCODE>code</THINKCODE>"), "thinking inline code should use thinking code color");
assert(!thinking.includes("<CODE>code</CODE>"), "thinking inline code should not fall back to normal code color");

const ansiTheme: MarkdownTheme = {
	...theme,
	italic: text => `\x1b[3m${text}\x1b[23m`,
};
const longThinking = new Markdown(
	"I got search results but I need to look at the actual weather data. Let me fetch the BOM pages for Canberra and Melbourne, as they are the most authoritative sources for Australian weather.",
	0,
	0,
	ansiTheme,
	{
		color: text => `\x1b[38;2;120;130;150m${text}\x1b[39m`,
		italic: true,
	},
).render(72);

assert(longThinking.length > 1, "long thinking text should wrap for the regression check");
for (const line of longThinking) {
	assert(line.startsWith("\x1b[3m\x1b[38;2;120;130;150m"), "wrapped thinking line should reapply italic and color");
}
assert(
	longThinking.some(line => line.includes("authoritative")),
	"wrapped thinking regression fixture should include the reported word",
);

console.log("PASS: markdown thinking code style");
