import { Markdown, renderInlineMarkdown, type MarkdownTheme } from "../src/active-port/tui/components/markdown.js";
import { assert } from "./helpers.js";

const theme: MarkdownTheme = {
	heading: text => text,
	link: text => `\x1b[31m${text}\x1b[39m`,
	linkUrl: text => text,
	code: text => text,
	codeBlock: text => text,
	codeBlockBorder: text => text,
	quote: text => text,
	quoteBorder: text => text,
	hr: text => text,
	listBullet: text => text,
	bold: text => text,
	italic: text => text,
	strikethrough: text => text,
	underline: text => `\x1b[4m${text}\x1b[24m`,
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

const text = "https://acm.hdu.edu.cn/showproblem.php?pid=1087，还挺难的，Java怎么解？";
const rendered = new Markdown(text, 0, 0, theme, { color: value => `\x1b[37m${value}\x1b[39m` }).render(120).join("\n");
assert(
	rendered.includes("\x1b]8;;https://acm.hdu.edu.cn/showproblem.php?pid=1087\x07"),
	"bare URL link should stop before Chinese punctuation",
);
assert(!rendered.includes("\x1b]8;;https://acm.hdu.edu.cn/showproblem.php?pid=1087，"), "hyperlink target must not include Chinese punctuation");
assert(rendered.includes("\x1b]8;;\x07\x1b[37m"), "Chinese text after URL should render after the hyperlink closes");
assert(Bun.stripANSI(rendered).includes(text), "visible text should stay unchanged");

const inline = renderInlineMarkdown(text, theme, value => `\x1b[37m${value}\x1b[39m`);
assert(!inline.includes("\x1b[37mhttps://acm.hdu.edu.cn/showproblem.php?pid=1087，还"), "inline markdown link should stop before Chinese punctuation");
assert(inline.includes("\x1b[37m，还挺难的，Java怎么解？\x1b[39m"), "inline markdown should keep trailing text normal");

console.log("PASS: markdown autolink punctuation");
