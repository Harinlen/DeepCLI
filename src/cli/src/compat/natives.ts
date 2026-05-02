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
	return String(text ?? "").split("\n").flatMap(line => {
		const chunks: string[] = [];
		let current = "";
		for (const char of [...line]) {
			if (visibleWidth(current + char) > width && current) {
				chunks.push(current);
				current = char;
			} else {
				current += char;
			}
		}
		chunks.push(current);
		return chunks;
	});
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
