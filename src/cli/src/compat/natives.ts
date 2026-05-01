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
export function highlightCode(code: string): string { return code; }
export function supportsLanguage(_language: string): boolean { return false; }

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
