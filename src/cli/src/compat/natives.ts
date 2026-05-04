export type KeyEventType = "press" | "repeat" | "release";
export type Ellipsis = "none" | "end" | "start" | "middle" | undefined | null;
export enum FileType { File = "file", Directory = "directory", Symlink = "symlink" }

export interface SliceResult { text: string; width: number }
export interface ExtractSegmentsResult {
	before: string;
	beforeWidth: number;
	segments: Array<{ text: string; width: number; type?: string }>;
	after: string;
	afterWidth: number;
	width: number;
}

export const Ellipsis = {
	None: "none",
	End: "end",
	Start: "start",
	Middle: "middle",
	Omit: "none",
} as const;

const ANSI_RE = /\x1b\[[0-9;?]*[ -/]*[@-~]/g;
const ANSI_TOKEN_RE = /\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)/g;

function plain(text: string): string {
	return String(text ?? "").replace(ANSI_RE, "");
}

export function sanitizeText(text: string): string {
	return String(text ?? "").replace(/\x00/g, "");
}

export function visibleWidth(text: string): number {
	return Bun.stringWidth?.(plain(text)) ?? [...plain(text)].length;
}

export const stringWidth = visibleWidth;

export function sliceWithWidth(text: string, startCol: number, length: number, ..._rest: unknown[]): SliceResult {
	const chars = [...plain(text)];
	const sliced = chars.slice(Math.max(0, startCol), Math.max(0, startCol + length)).join("");
	return { text: sliced, width: visibleWidth(sliced) };
}

export function truncateToWidth(text: string, width: number, ellipsisKind: Ellipsis = "end", pad?: boolean | null, ..._rest: unknown[]): string {
	const source = plain(text);
	if (visibleWidth(source) <= width) return pad ? source.padEnd(width) : source;
	if (width <= 0) return "";
	const ellipsis = (ellipsisKind as unknown) === "none" || ellipsisKind === Ellipsis.Omit ? "" : "…";
	const max = Math.max(0, width - visibleWidth(ellipsis));
	const result = [...source].slice(0, max).join("") + ellipsis;
	return pad ? result.padEnd(width) : result;
}

export function wrapTextWithAnsi(text: string, width: number, ..._rest: unknown[]): string[] {
	const maxWidth = Math.max(1, width);
	return String(text ?? "").split("\n").flatMap(line => wrapAnsiLine(line, maxWidth));
}

interface SgrState {
	bold: boolean;
	italic: boolean;
	underline: boolean;
	strikethrough: boolean;
	fg?: string;
	bg?: string;
}

const sgrReset = "\x1b[0m";
const graphemeSegmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });

function wrapAnsiLine(line: string, width: number): string[] {
	const chunks: string[] = [];
	const state: SgrState = { bold: false, italic: false, underline: false, strikethrough: false };
	let current = "";
	let currentWidth = 0;
	let lastIndex = 0;

	const appendText = (value: string) => {
		for (const part of graphemeSegmenter.segment(value)) {
			const grapheme = part.segment;
			const graphemeWidth = visibleWidth(grapheme);
			if (currentWidth > 0 && currentWidth + graphemeWidth > width) {
				chunks.push(closeChunk(current, state));
				current = statePrefix(state);
				currentWidth = 0;
			}
			current += grapheme;
			currentWidth += graphemeWidth;
		}
	};

	for (const match of line.matchAll(ANSI_TOKEN_RE)) {
		appendText(line.slice(lastIndex, match.index));
		const token = match[0];
		current += token;
		updateSgrState(state, token);
		lastIndex = match.index + token.length;
	}
	appendText(line.slice(lastIndex));

	chunks.push(current);
	return chunks;
}

function closeChunk(chunk: string, state: SgrState): string {
	return statePrefix(state) ? `${chunk}${sgrReset}` : chunk;
}

function statePrefix(state: SgrState): string {
	let prefix = "";
	if (state.bold) prefix += "\x1b[1m";
	if (state.italic) prefix += "\x1b[3m";
	if (state.underline) prefix += "\x1b[4m";
	if (state.strikethrough) prefix += "\x1b[9m";
	if (state.fg) prefix += state.fg;
	if (state.bg) prefix += state.bg;
	return prefix;
}

function updateSgrState(state: SgrState, token: string): void {
	const match = /^\x1b\[([0-9;]*)m$/.exec(token);
	if (!match) return;

	const codes = match[1] === "" ? [0] : match[1].split(";").map(code => Number(code || 0));
	for (let i = 0; i < codes.length; i++) {
		const code = codes[i];
		switch (code) {
			case 0:
				state.bold = false;
				state.italic = false;
				state.underline = false;
				state.strikethrough = false;
				state.fg = undefined;
				state.bg = undefined;
				break;
			case 1:
				state.bold = true;
				break;
			case 3:
				state.italic = true;
				break;
			case 4:
				state.underline = true;
				break;
			case 9:
				state.strikethrough = true;
				break;
			case 22:
				state.bold = false;
				break;
			case 23:
				state.italic = false;
				break;
			case 24:
				state.underline = false;
				break;
			case 29:
				state.strikethrough = false;
				break;
			case 39:
				state.fg = undefined;
				break;
			case 49:
				state.bg = undefined;
				break;
			case 38:
			case 48: {
				const parsed = parseExtendedColor(codes, i);
				if (parsed) {
					if (code === 38) state.fg = `\x1b[${parsed.sequence}m`;
					else state.bg = `\x1b[${parsed.sequence}m`;
					i = parsed.nextIndex;
				}
				break;
			}
			default:
				if ((code >= 30 && code <= 37) || (code >= 90 && code <= 97)) {
					state.fg = `\x1b[${code}m`;
				} else if ((code >= 40 && code <= 47) || (code >= 100 && code <= 107)) {
					state.bg = `\x1b[${code}m`;
				}
		}
	}
}

function parseExtendedColor(codes: number[], startIndex: number): { sequence: string; nextIndex: number } | undefined {
	const mode = codes[startIndex + 1];
	if (mode === 5 && Number.isFinite(codes[startIndex + 2])) {
		return {
			sequence: `${codes[startIndex]};5;${codes[startIndex + 2]}`,
			nextIndex: startIndex + 2,
		};
	}
	if (
		mode === 2 &&
		Number.isFinite(codes[startIndex + 2]) &&
		Number.isFinite(codes[startIndex + 3]) &&
		Number.isFinite(codes[startIndex + 4])
	) {
		return {
			sequence: `${codes[startIndex]};2;${codes[startIndex + 2]};${codes[startIndex + 3]};${codes[startIndex + 4]}`,
			nextIndex: startIndex + 4,
		};
	}
	return undefined;
}

export function extractSegments(
	line: string,
	beforeEnd = 0,
	afterStart = 0,
	afterLen = Math.max(0, line.length - afterStart),
	..._rest: unknown[]
): ExtractSegmentsResult {
	const before = sliceWithWidth(line, 0, beforeEnd).text;
	const after = sliceWithWidth(line, afterStart, afterLen).text;
	const middle = plain(line).slice(before.length, Math.max(before.length, plain(line).length - after.length));
	return {
		before,
		beforeWidth: visibleWidth(before),
		segments: middle ? [{ text: middle, width: visibleWidth(middle) }] : [],
		after,
		afterWidth: visibleWidth(after),
		width: visibleWidth(line),
	};
}

export interface ParsedKittySequence {
	codepoint: number;
	shiftedKey?: number;
	baseLayoutKey?: number;
	modifier: number;
	eventType?: KeyEventType;
}

export function parseKittySequence(_data: string, ..._rest: unknown[]): ParsedKittySequence | null {
	return null;
}

export function parseKey(data: string, ..._rest: unknown[]): string | undefined {
	const map: Record<string, string> = {
		"\x03": "ctrl+c",
		"\x04": "ctrl+d",
		"\x0c": "ctrl+l",
		"\x12": "ctrl+r",
		"\r": "enter",
		"\n": "enter",
		"\t": "tab",
		"\x1b": "escape",
		"\x7f": "backspace",
		"\x1b[A": "up",
		"\x1b[B": "down",
		"\x1b[C": "right",
		"\x1b[D": "left",
		"\x1b[Z": "shift+tab",
	};
	const mapped = map[data];
	if (mapped) return mapped;
	if (data.length === 1) {
		const code = data.charCodeAt(0);
		if (code >= 1 && code <= 26) {
			return `ctrl+${String.fromCharCode(code + 96)}`;
		}
		return data;
	}
	return undefined;
}

export function matchesKey(data: string, key: string, ..._rest: unknown[]): boolean {
	return parseKey(data) === key || data === key;
}

export function setKittyProtocolActive(_active: boolean): void {}

export async function fuzzyFind(profile: { query?: string; searchPath?: string } | string): Promise<{ matches: Array<{ path: string; isDirectory?: boolean }> }> {
	const query = typeof profile === "string" ? profile : (profile.query ?? "");
	return { matches: query ? [{ path: query, isDirectory: false }] : [] };
}

export async function glob(_pattern: string | string[], _options?: unknown): Promise<string[]> {
	return [];
}

export function encodeSixel(_data: Uint8Array, ..._rest: unknown[]): string { return ""; }
export function detectMacOSAppearance(): "dark" | "light" { return "dark"; }
export class MacAppearanceObserver { start(): void {}; stop(): void {}; onChange(_cb: unknown): void {} }
export type HighlightColors = Record<string, string>;

const RESET = "\x1b[0m";

const LANGUAGE_ALIASES: Record<string, string> = {
	c: "c",
	cc: "cpp",
	cxx: "cpp",
	cpp: "cpp",
	"c++": "cpp",
	h: "cpp",
	hh: "cpp",
	hpp: "cpp",
	hxx: "cpp",
	js: "javascript",
	jsx: "javascript",
	javascript: "javascript",
	mjs: "javascript",
	cjs: "javascript",
	ts: "typescript",
	tsx: "typescript",
	typescript: "typescript",
	py: "python",
	python: "python",
	sh: "shell",
	bash: "shell",
	zsh: "shell",
	shell: "shell",
	json: "json",
	jsonc: "json",
	yaml: "yaml",
	yml: "yaml",
};

const C_LIKE_KEYWORDS = new Set([
	"asm",
	"auto",
	"break",
	"case",
	"catch",
	"class",
	"concept",
	"const",
	"constexpr",
	"continue",
	"decltype",
	"default",
	"delete",
	"do",
	"else",
	"enum",
	"explicit",
	"export",
	"extern",
	"for",
	"friend",
	"goto",
	"if",
	"inline",
	"namespace",
	"new",
	"noexcept",
	"operator",
	"private",
	"protected",
	"public",
	"requires",
	"return",
	"sizeof",
	"static",
	"struct",
	"switch",
	"template",
	"this",
	"throw",
	"try",
	"typedef",
	"typeid",
	"typename",
	"using",
	"virtual",
	"while",
]);

const C_LIKE_TYPES = new Set([
	"bool",
	"char",
	"double",
	"float",
	"int",
	"long",
	"short",
	"signed",
	"size_t",
	"std",
	"string",
	"uint32_t",
	"uint64_t",
	"unsigned",
	"void",
	"vector",
]);

const JS_KEYWORDS = new Set([
	"async",
	"await",
	"break",
	"case",
	"catch",
	"class",
	"const",
	"continue",
	"default",
	"delete",
	"do",
	"else",
	"export",
	"extends",
	"finally",
	"for",
	"from",
	"function",
	"if",
	"import",
	"in",
	"instanceof",
	"let",
	"new",
	"of",
	"return",
	"static",
	"switch",
	"throw",
	"try",
	"type",
	"typeof",
	"var",
	"while",
	"yield",
]);

const PYTHON_KEYWORDS = new Set([
	"and",
	"as",
	"assert",
	"async",
	"await",
	"break",
	"class",
	"continue",
	"def",
	"del",
	"elif",
	"else",
	"except",
	"False",
	"finally",
	"for",
	"from",
	"global",
	"if",
	"import",
	"in",
	"is",
	"lambda",
	"None",
	"nonlocal",
	"not",
	"or",
	"pass",
	"raise",
	"return",
	"True",
	"try",
	"while",
	"with",
	"yield",
]);

function normalizeLanguage(language: string | undefined): string | undefined {
	const key = String(language ?? "").trim().toLowerCase();
	return key ? LANGUAGE_ALIASES[key] : undefined;
}

function color(text: string, ansi: string | undefined): string {
	return ansi ? `${ansi}${text}${RESET}` : text;
}

function readString(line: string, start: number, quote: string): number {
	let index = start + 1;
	while (index < line.length) {
		if (line[index] === "\\") {
			index += 2;
			continue;
		}
		if (line[index] === quote) return index + 1;
		index++;
	}
	return line.length;
}

function highlightCLike(code: string, colors: HighlightColors): string {
	let inBlockComment = false;
	return code
		.split("\n")
		.map(line => {
			let output = "";
			let index = 0;
			while (index < line.length) {
				if (inBlockComment) {
					const end = line.indexOf("*/", index);
					const next = end === -1 ? line.length : end + 2;
					output += color(line.slice(index, next), colors.comment);
					index = next;
					if (end !== -1) inBlockComment = false;
					continue;
				}

				const rest = line.slice(index);
				if (rest.startsWith("//")) {
					output += color(rest, colors.comment);
					break;
				}
				if (rest.startsWith("/*")) {
					const end = line.indexOf("*/", index + 2);
					const next = end === -1 ? line.length : end + 2;
					output += color(line.slice(index, next), colors.comment);
					index = next;
					inBlockComment = end === -1;
					continue;
				}
				if (line[index] === "\"" || line[index] === "'") {
					const end = readString(line, index, line[index]);
					output += color(line.slice(index, end), colors.string);
					index = end;
					continue;
				}

				const numberMatch = rest.match(/^(?:0x[\da-fA-F]+|\d+(?:\.\d+)?)(?:[uUlLfF]+)?/);
				if (numberMatch) {
					output += color(numberMatch[0], colors.number);
					index += numberMatch[0].length;
					continue;
				}

				const identMatch = rest.match(/^[A-Za-z_][A-Za-z0-9_]*/);
				if (identMatch) {
					const word = identMatch[0];
					const after = line.slice(index + word.length);
					if (C_LIKE_KEYWORDS.has(word) || (word.startsWith("#") && word.length > 1)) {
						output += color(word, colors.keyword);
					} else if (C_LIKE_TYPES.has(word)) {
						output += color(word, colors.type);
					} else if (/^\s*\(/.test(after)) {
						output += color(word, colors.function);
					} else {
						output += word;
					}
					index += word.length;
					continue;
				}

				const char = line[index];
				output += /[{}()[\],.;:]/.test(char)
					? color(char, colors.punctuation)
					: /[+\-*/%=!<>&|^~?]/.test(char)
						? color(char, colors.operator)
						: char;
				index++;
			}
			return output;
		})
		.join("\n");
}

function highlightScriptLike(code: string, colors: HighlightColors, keywords: Set<string>, commentPrefix = "#"): string {
	return code
		.split("\n")
		.map(line => {
			let output = "";
			let index = 0;
			while (index < line.length) {
				const rest = line.slice(index);
				if (rest.startsWith(commentPrefix)) {
					output += color(rest, colors.comment);
					break;
				}
				if (line[index] === "\"" || line[index] === "'" || line[index] === "`") {
					const end = readString(line, index, line[index]);
					output += color(line.slice(index, end), colors.string);
					index = end;
					continue;
				}
				const numberMatch = rest.match(/^\d+(?:\.\d+)?/);
				if (numberMatch) {
					output += color(numberMatch[0], colors.number);
					index += numberMatch[0].length;
					continue;
				}
				const identMatch = rest.match(/^[A-Za-z_$][A-Za-z0-9_$]*/);
				if (identMatch) {
					const word = identMatch[0];
					const after = line.slice(index + word.length);
					if (keywords.has(word)) {
						output += color(word, colors.keyword);
					} else if (/^\s*\(/.test(after)) {
						output += color(word, colors.function);
					} else {
						output += word;
					}
					index += word.length;
					continue;
				}
				const char = line[index];
				output += /[{}()[\],.;:]/.test(char)
					? color(char, colors.punctuation)
					: /[+\-*/%=!<>&|^~?]/.test(char)
						? color(char, colors.operator)
						: char;
				index++;
			}
			return output;
		})
		.join("\n");
}

function highlightData(code: string, colors: HighlightColors): string {
	return code
		.replace(/("(?:\\.|[^"\\])*")(\s*:)?/g, (_match, str: string, colon: string | undefined) => {
			return `${color(str, colors.string)}${colon ? color(colon, colors.punctuation) : ""}`;
		})
		.replace(/\b(true|false|null)\b/g, value => color(value, colors.keyword))
		.replace(/\b-?\d+(?:\.\d+)?\b/g, value => color(value, colors.number));
}

export function highlightCode(code: string, language?: string, colors: HighlightColors = {}): string {
	const normalized = normalizeLanguage(language);
	switch (normalized) {
		case "c":
		case "cpp":
			return highlightCLike(code, colors);
		case "javascript":
		case "typescript":
			return highlightScriptLike(code, colors, JS_KEYWORDS, "//");
		case "python":
			return highlightScriptLike(code, colors, PYTHON_KEYWORDS);
		case "shell":
			return highlightScriptLike(code, colors, new Set(["case", "do", "done", "elif", "else", "esac", "fi", "for", "function", "if", "in", "then", "while"]));
		case "json":
		case "yaml":
			return highlightData(code, colors);
		default:
			return code;
	}
}

export function supportsLanguage(language: string): boolean {
	return normalizeLanguage(language) !== undefined;
}

export interface ClipboardImage { data: Uint8Array; mimeType: string }
export async function copyToClipboard(_text: string): Promise<void> {}
export async function readImageFromClipboard(): Promise<ClipboardImage | null> { return null; }

export type ImageFormat = "png" | "jpeg" | "webp";
export const ImageFormat = { Png: "png", Jpeg: "jpeg", Webp: "webp" } as const;
export enum SamplingFilter { Nearest = "nearest", Triangle = "triangle", CatmullRom = "catmullRom" }
export class PhotonImage {
	static new_from_byteslice(data: Uint8Array): PhotonImage { return new PhotonImage(data); }
	constructor(public data: Uint8Array = new Uint8Array()) {}
	get_width(): number { return 0; }
	get_height(): number { return 0; }
	get_bytes(): Uint8Array { return this.data; }
}

export function resize(_image: PhotonImage, _width: number, _height: number, _filter?: SamplingFilter): PhotonImage {
	return _image;
}

export function killTree(_pid: number): void {}
export function getWorkProfile(): Record<string, unknown> { return {}; }
