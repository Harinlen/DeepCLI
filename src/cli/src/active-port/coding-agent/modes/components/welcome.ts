import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { defaultConfigDir, defaultDataDir } from "@/config/paths.js";
import { type Component, padding, truncateToWidth, visibleWidth } from "@/tui/index.js";
import { APP_NAME } from "@/compat/utils.js";
import { theme } from "../../modes/theme/theme";

const DEFAULT_WELCOME_LOGO = [
	"     ⢀⣀  ⢀    ",
	"⣠⣶⣿⣿⣿⣿⣿⣶⣄⡘⢿⣶⡶⠟",
	"⣿⡀ ⠈⠙⠻⣿⣿⣙⣿⣿⠇  ",
	"⠈⠻⣦⣀⣰⣤⣈⡻⣿⣿⣋   ",
	"   ⠉⠉⠉⠉⠉      ",
];
const WELCOME_LOGO = readWelcomeLogo();

export interface RecentSession {
	name: string;
	timeAgo: string;
}

export interface LspServerInfo {
	name: string;
	status: "ready" | "error" | "connecting";
	fileTypes: string[];
}

/**
 * Premium welcome screen with block-based OMP logo and two-column layout.
 */
export class WelcomeComponent implements Component {
	constructor(
		private readonly version: string,
		private modelName: string,
		private providerName: string,
		private recentSessions: RecentSession[] = [],
		private lspServers: LspServerInfo[] = [],
	) {}

	invalidate(): void {}

	setModel(modelName: string, providerName: string): void {
		this.modelName = modelName;
		this.providerName = providerName;
	}

	setRecentSessions(sessions: RecentSession[]): void {
		this.recentSessions = sessions;
	}

	setLspServers(servers: LspServerInfo[]): void {
		this.lspServers = servers;
	}

	render(termWidth: number): string[] {
		// Box dimensions - responsive with max width and small-terminal support
		const maxWidth = 100;
		const boxWidth = Math.min(maxWidth, Math.max(0, termWidth - 2));
		if (boxWidth < 4) {
			return [];
		}
		const dualContentWidth = boxWidth - 3; // 3 = │ + │ + │
		const preferredLeftCol = 26;
		const minLeftCol = 14; // logo width
		const minRightCol = 20;
		const leftMinContentWidth = Math.max(
			minLeftCol,
			visibleWidth("Welcome back!"),
		);
		const desiredLeftCol = Math.min(preferredLeftCol, Math.max(minLeftCol, Math.floor(dualContentWidth * 0.35)));
		const dualLeftCol =
			dualContentWidth >= minRightCol + 1
				? Math.min(desiredLeftCol, dualContentWidth - minRightCol)
				: Math.max(1, dualContentWidth - 1);
		const dualRightCol = Math.max(1, dualContentWidth - dualLeftCol);
		const showRightColumn = dualLeftCol >= leftMinContentWidth && dualRightCol >= minRightCol;
		const leftCol = showRightColumn ? dualLeftCol : boxWidth - 2;
		const rightCol = showRightColumn ? dualRightCol : 0;

		// Apply gradient to logo
		const logoColored = WELCOME_LOGO.map(line => this.#gradientLine(line));

		// Left column - centered content
		const leftLines = [
			"",
			this.#centerText(theme.bold("Welcome back!"), leftCol),
			"",
			...logoColored.map(l => this.#centerText(l, leftCol)),
			"",
			this.#centerText(theme.fg("muted", this.modelName), leftCol),
			this.#centerText(theme.fg("borderMuted", this.providerName), leftCol),
		];

		// Right column separator
		const separatorWidth = Math.max(0, rightCol - 2); // padding on each side
		const separator = ` ${theme.fg("dim", theme.boxRound.horizontal.repeat(separatorWidth))}`;

		// Recent sessions content
		const sessionLines: string[] = [];
		if (this.recentSessions.length === 0) {
			sessionLines.push(` ${theme.fg("dim", "No recent sessions")}`);
		} else {
			for (const session of this.recentSessions.slice(0, 3)) {
				const name = displayRecentSessionName(session.name);
				sessionLines.push(
					` ${theme.fg("dim", `${theme.md.bullet} `)}${theme.fg("muted", name)}${theme.fg("dim", ` (${session.timeAgo})`)}`,
				);
			}
		}

		// Right column
		const rightLines = [
			` ${theme.bold(theme.fg("accent", "Tips"))}`,
			` ${theme.fg("dim", "?")}${theme.fg("muted", " for keyboard shortcuts")}`,
			` ${theme.fg("dim", "#")}${theme.fg("muted", " for prompt actions")}`,
			` ${theme.fg("dim", "/")}${theme.fg("muted", " for commands")}`,
			` ${theme.fg("dim", "!")}${theme.fg("muted", " to run bash")}`,
			` ${theme.fg("dim", "$")}${theme.fg("muted", " to run python")}`,
			separator,
			` ${theme.bold(theme.fg("accent", "Recent sessions"))}`,
			...sessionLines,
			"",
		];

		// Border characters (dim)
		const hChar = theme.boxRound.horizontal;
		const h = theme.fg("dim", hChar);
		const v = theme.fg("dim", theme.boxRound.vertical);
		const tl = theme.fg("dim", theme.boxRound.topLeft);
		const tr = theme.fg("dim", theme.boxRound.topRight);
		const bl = theme.fg("dim", theme.boxRound.bottomLeft);
		const br = theme.fg("dim", theme.boxRound.bottomRight);

		const lines: string[] = [];

		// Top border with embedded title
		const title = ` ${APP_NAME} v${this.version} `;
		const titlePrefixRaw = hChar.repeat(3);
		const titleStyled = theme.fg("dim", titlePrefixRaw) + theme.fg("muted", title);
		const titleVisLen = visibleWidth(titlePrefixRaw) + visibleWidth(title);
		const titleSpace = boxWidth - 2;
		if (titleVisLen >= titleSpace) {
			lines.push(tl + truncateToWidth(titleStyled, titleSpace) + tr);
		} else {
			const afterTitle = titleSpace - titleVisLen;
			lines.push(tl + titleStyled + theme.fg("dim", hChar.repeat(afterTitle)) + tr);
		}

		// Content rows
		const maxRows = showRightColumn ? Math.max(leftLines.length, rightLines.length) : leftLines.length;
		for (let i = 0; i < maxRows; i++) {
			const left = this.#fitToWidth(leftLines[i] ?? "", leftCol);
			if (showRightColumn) {
				const right = this.#fitToWidth(rightLines[i] ?? "", rightCol);
				lines.push(v + left + v + right + v);
			} else {
				lines.push(v + left + v);
			}
		}
		// Bottom border
		if (showRightColumn) {
			lines.push(bl + h.repeat(leftCol) + theme.fg("dim", theme.boxSharp.teeUp) + h.repeat(rightCol) + br);
		} else {
			lines.push(bl + h.repeat(leftCol) + br);
		}

		return lines;
	}

	/** Center text within a given width */
	#centerText(text: string, width: number): string {
		const visLen = visibleWidth(text);
		if (visLen >= width) {
			return truncateToWidth(text, width);
		}
		const leftPad = Math.floor((width - visLen) / 2);
		const rightPad = width - visLen - leftPad;
		return padding(leftPad) + text + padding(rightPad);
	}

	/** Apply the DeepCLI logo color to a string */
	#gradientLine(line: string): string {
		let result = "";
		for (const char of line) {
			result += char === " " ? char : theme.fg("accent", char);
		}
		return result;
	}

	/** Fit string to exact width with ANSI-aware truncation/padding */
	#fitToWidth(str: string, width: number): string {
		const visLen = visibleWidth(str);
		if (visLen > width) {
			const ellipsis = "…";
			const ellipsisWidth = visibleWidth(ellipsis);
			const maxWidth = Math.max(0, width - ellipsisWidth);
			let truncated = "";
			let currentWidth = 0;
			for (let i = 0; i < str.length;) {
				if (str[i] === "\x1b") {
					const ansiMatch = /^\x1b\[[0-9;?]*[ -/]*[@-~]/.exec(str.slice(i));
					if (ansiMatch) {
						truncated += ansiMatch[0];
						i += ansiMatch[0].length;
						continue;
					}
				}

				const codePoint = str.codePointAt(i);
				if (codePoint === undefined) break;
				const char = String.fromCodePoint(codePoint);
				const charWidth = visibleWidth(char);
				if (currentWidth + charWidth > maxWidth) {
					break;
				}
				truncated += char;
				currentWidth += charWidth;
				i += char.length;
			}
			const fitted = `${truncated}${ellipsis}`;
			return fitted + padding(Math.max(0, width - visibleWidth(fitted)));
		}
		return str + padding(width - visLen);
	}
}

function readWelcomeLogo(): string[] {
	for (const path of welcomeLogoCandidates()) {
		try {
			if (!existsSync(path)) continue;
			const text = readFileSync(path, "utf8").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
			const lines = text.split("\n");
			if (lines.at(-1) === "") lines.pop();
			if (lines.length > 0) return lines;
		} catch {
			// A broken user logo should not blank the Welcome screen.
		}
	}
	return DEFAULT_WELCOME_LOGO;
}

function welcomeLogoCandidates(): string[] {
	const configDir = defaultConfigDir();
	const dataDir = defaultDataDir();
	return [
		process.env.DEEPCLI_WELCOME_LOGO_FILE,
		join(configDir, "welcome-logo.txt"),
		join(configDir, "ui", "welcome-logo.txt"),
		join(dataDir, "assets", "welcome-logo.txt"),
	].filter((path): path is string => Boolean(path));
}

function displayRecentSessionName(name: string): string {
	return (
		name
			.replace(/<system-reminder\b[^>]*>[\s\S]*?<\/system-reminder>/g, " ")
			.replace(/<system-reminder\b[^>]*>[\s\S]*$/g, " ")
			.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, " ")
			.split(/\s+/)
			.filter(Boolean)
			.join(" ") || "Untitled session"
	);
}
