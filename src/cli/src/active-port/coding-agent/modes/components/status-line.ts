// @ts-nocheck
import * as fs from "node:fs";
import { type Component, truncateToWidth, visibleWidth } from "@/tui/index.js";
import { formatCount, getProjectDir } from "@/compat/utils.js";
import { $ } from "bun";
import { settings } from "../../config/settings";
import type { StatusLinePreset, StatusLineSegmentId, StatusLineSeparatorStyle } from "../../config/settings-schema";
import { theme } from "../../modes/theme/theme";
import type { AgentSession } from "../../session/agent-session";
import * as git from "../../utils/git";
import { sanitizeStatusText } from "../shared";
import {
	canReuseCachedPr,
	createPrCacheContext,
	isSamePrCacheContext,
	type PrCacheContext,
} from "./status-line/git-utils";
import { getPreset } from "./status-line/presets";
import { renderSegment, type SegmentContext } from "./status-line/segments";
import { getSeparator } from "./status-line/separators";

export interface StatusLineSegmentOptions {
	model?: { showThinkingLevel?: boolean };
	path?: { abbreviate?: boolean; maxLength?: number; stripWorkPrefix?: boolean };
	git?: { showBranch?: boolean; showStaged?: boolean; showUnstaged?: boolean; showUntracked?: boolean };
	time?: { format?: "12h" | "24h"; showSeconds?: boolean };
}

export interface StatusLineSettings {
	preset?: StatusLinePreset;
	leftSegments?: StatusLineSegmentId[];
	rightSegments?: StatusLineSegmentId[];
	separator?: StatusLineSeparatorStyle;
	segmentOptions?: StatusLineSegmentOptions;
	showHookStatus?: boolean;
}

// ═══════════════════════════════════════════════════════════════════════════
// Rendering Helpers
// ═══════════════════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════════════════
// StatusLineComponent
// ═══════════════════════════════════════════════════════════════════════════

export class StatusLineComponent implements Component {
	#settings: StatusLineSettings = {};
	#cachedBranch: string | null | undefined = undefined;
	#cachedBranchRepoId: string | null | undefined = undefined;
	#gitWatcher: fs.FSWatcher | null = null;
	#onBranchChange: (() => void) | null = null;
	#autoCompactEnabled: boolean = true;
	#hookStatuses: Map<string, string> = new Map();
	#subagentCount: number = 0;
	#sessionStartTime: number = Date.now();
	#planModeStatus: { enabled: boolean; paused: boolean } | null = null;

	// Git status caching (1s TTL)
	#cachedGitStatus: { staged: number; unstaged: number; untracked: number } | null = null;
	#gitStatusLastFetch = 0;
	#gitStatusInFlight = false;

	// PR lookup caching (invalidated on branch/repo context changes)
	#cachedPr: { number: number; url: string } | null | undefined = undefined;
	#cachedPrContext: PrCacheContext | undefined = undefined;
	#prLookupInFlight = false;
	#defaultBranch?: string;

	constructor(private readonly session: AgentSession) {
		this.#settings = {
			preset: settings.get("statusLine.preset"),
			leftSegments: settings.get("statusLine.leftSegments"),
			rightSegments: settings.get("statusLine.rightSegments"),
			separator: settings.get("statusLine.separator"),
			showHookStatus: settings.get("statusLine.showHookStatus"),
			segmentOptions: settings.getGroup("statusLine").segmentOptions,
		};
	}

	updateSettings(settings: StatusLineSettings): void {
		this.#settings = settings;
	}

	setAutoCompactEnabled(enabled: boolean): void {
		this.#autoCompactEnabled = enabled;
	}

	setSubagentCount(count: number): void {
		this.#subagentCount = count;
	}

	setSessionStartTime(time: number): void {
		this.#sessionStartTime = time;
	}

	setPlanModeStatus(status: { enabled: boolean; paused: boolean } | undefined): void {
		this.#planModeStatus = status ?? null;
	}

	setHookStatus(key: string, text: string | undefined): void {
		if (text === undefined) {
			this.#hookStatuses.delete(key);
		} else {
			this.#hookStatuses.set(key, text);
		}
	}

	watchBranch(onBranchChange: () => void): void {
		this.#onBranchChange = onBranchChange;
		this.#setupGitWatcher();
	}

	#setupGitWatcher(): void {
		if (this.#gitWatcher) {
			this.#gitWatcher.close();
			this.#gitWatcher = null;
		}

		const gitHeadPath = git.repo.resolveSync(getProjectDir())?.headPath ?? null;
		if (!gitHeadPath) return;

		try {
			this.#gitWatcher = fs.watch(gitHeadPath, () => {
				this.#invalidateGitCaches();
				if (this.#onBranchChange) {
					this.#onBranchChange();
				}
			});
		} catch {
			this.#invalidateGitCaches();
		}
	}

	dispose(): void {
		if (this.#gitWatcher) {
			this.#gitWatcher.close();
			this.#gitWatcher = null;
		}
	}

	invalidate(): void {
		this.#invalidateGitCaches();
	}

	#invalidateGitCaches(): void {
		this.#cachedBranch = undefined;
		this.#cachedBranchRepoId = undefined;
		this.#cachedPrContext = undefined;
	}
	#getCurrentBranch(): string | null {
		const head = git.head.resolveSync(getProjectDir());
		const gitHeadPath = head?.headPath ?? null;
		if (this.#cachedBranch !== undefined && this.#cachedBranchRepoId === gitHeadPath) {
			return this.#cachedBranch;
		}

		this.#cachedBranchRepoId = gitHeadPath;
		if (!head) {
			this.#cachedBranch = null;
			return null;
		}

		this.#cachedBranch = head.kind === "ref" ? (head.branchName ?? head.ref) : "detached";

		return this.#cachedBranch ?? null;
	}

	#isDefaultBranch(branch: string): boolean {
		if (this.#defaultBranch === undefined) {
			this.#defaultBranch = "main";
			(async () => {
				const resolved = await git.branch.default(getProjectDir());
				if (resolved) {
					this.#defaultBranch = resolved;
					if (this.#onBranchChange) {
						this.#onBranchChange();
					}
				}
			})();
		}
		return branch === this.#defaultBranch;
	}

	#getGitStatus(): { staged: number; unstaged: number; untracked: number } | null {
		if (this.#gitStatusInFlight || Date.now() - this.#gitStatusLastFetch < 1000) {
			return this.#cachedGitStatus;
		}

		this.#gitStatusInFlight = true;

		(async () => {
			try {
				this.#cachedGitStatus = await git.status.summary(getProjectDir());
			} catch {
				this.#cachedGitStatus = null;
			} finally {
				this.#gitStatusLastFetch = Date.now();
				this.#gitStatusInFlight = false;
			}
		})();

		return this.#cachedGitStatus;
	}

	#lookupPr(): { number: number; url: string } | null {
		const branch = this.#getCurrentBranch();
		const currentContext = branch ? createPrCacheContext(branch, this.#cachedBranchRepoId ?? null) : null;

		if (canReuseCachedPr(this.#cachedPr, this.#cachedPrContext, currentContext)) {
			return this.#cachedPr ?? null;
		}

		const stalePr = this.#cachedPr;

		// Don't look up if no branch, detached HEAD, default branch, or already in flight
		if (!branch || branch === "detached" || this.#isDefaultBranch(branch) || this.#prLookupInFlight) {
			return stalePr ?? null;
		}

		this.#prLookupInFlight = true;
		const lookupContext = currentContext;

		// Fire async lookup, keep stale value visible until resolved
		(async () => {
			// Helper: only write cache if branch/repo context hasn't changed since launch
			const setCachedPr = (value: { number: number; url: string } | null) => {
				const latestBranch = this.#getCurrentBranch();
				const latestContext = latestBranch
					? createPrCacheContext(latestBranch, this.#cachedBranchRepoId ?? null)
					: undefined;
				if (lookupContext && isSamePrCacheContext(latestContext, lookupContext)) {
					this.#cachedPr = value;
					this.#cachedPrContext = lookupContext;
				}
			};
			try {
				// Requires `gh repo set-default` to be configured; fails gracefully if not
				const result = await $`gh pr view --json number,url`.quiet().nothrow();
				if (result.exitCode !== 0) {
					setCachedPr(null);
					return;
				}
				const pr = JSON.parse(result.stdout.toString()) as { number: number; url: string };
				if (typeof pr.number === "number") {
					setCachedPr({ number: pr.number, url: pr.url });
				} else {
					setCachedPr(null);
				}
			} catch {
				setCachedPr(null);
			} finally {
				this.#prLookupInFlight = false;
				if (this.#onBranchChange) {
					this.#onBranchChange();
				}
			}
		})();

		return stalePr ?? null;
	}

	#getLastTurnDurationMs(): number | null {
		for (let i = this.session.state.messages.length - 1; i >= 0; i--) {
			const message = this.session.state.messages[i];
			if (message?.role !== "assistant") continue;
			const duration = message.duration;
			if (typeof duration === "number" && Number.isFinite(duration) && duration > 0) {
				return duration;
			}
			if (this.session.isStreaming && typeof message.timestamp === "number") {
				const elapsed = Date.now() - message.timestamp;
				return elapsed > 0 ? elapsed : null;
			}
			return null;
		}
		return null;
	}

	#buildSegmentContext(width: number): SegmentContext {
		const state = this.session.state;

		const usageStats = {
			turnDurationMs: this.#getLastTurnDurationMs(),
		};

		const contextUsage = this.session.sessionManager?.getContextUsage?.() ?? { totalTokens: 0, contextWindow: null, percent: 0 };
		const contextTokens = contextUsage.totalTokens ?? 0;
		const contextWindow = contextUsage.contextWindow ?? state.model?.contextWindow ?? 0;
		const contextPercent = contextUsage.percent ?? (contextWindow > 0 ? (contextTokens / contextWindow) * 100 : 0);

		return {
			session: this.session,
			width,
			options: this.#resolveSettings().segmentOptions ?? {},
			planMode: this.#planModeStatus,
			usageStats,
			contextPercent,
			contextTokens,
			contextWindow,
			autoCompactEnabled: this.#autoCompactEnabled,
			subagentCount: this.#subagentCount,
			sessionStartTime: this.#sessionStartTime,
			git: {
				branch: this.#getCurrentBranch(),
				status: this.#getGitStatus(),
				pr: this.#lookupPr(),
			},
		};
	}

	#resolveSettings(): Required<
		Pick<StatusLineSettings, "leftSegments" | "rightSegments" | "separator" | "segmentOptions">
	> &
		StatusLineSettings {
		const preset = this.#settings.preset ?? "default";
		const presetDef = getPreset(preset);
		const useCustomSegments = preset === "custom";
		const mergedSegmentOptions: StatusLineSettings["segmentOptions"] = {};

		for (const [segment, options] of Object.entries(presetDef.segmentOptions ?? {})) {
			mergedSegmentOptions[segment as keyof StatusLineSegmentOptions] = { ...(options as Record<string, unknown>) };
		}

		for (const [segment, options] of Object.entries(this.#settings.segmentOptions ?? {})) {
			const current = mergedSegmentOptions[segment as keyof StatusLineSegmentOptions] ?? {};
			mergedSegmentOptions[segment as keyof StatusLineSegmentOptions] = {
				...(current as Record<string, unknown>),
				...(options as Record<string, unknown>),
			};
		}

		const leftSegments = useCustomSegments
			? (this.#settings.leftSegments ?? presetDef.leftSegments)
			: presetDef.leftSegments;
		const rightSegments = useCustomSegments
			? (this.#settings.rightSegments ?? presetDef.rightSegments)
			: presetDef.rightSegments;

		return {
			...this.#settings,
			leftSegments,
			rightSegments,
			separator: this.#settings.separator ?? presetDef.separator,
			segmentOptions: mergedSegmentOptions,
		};
	}

	#buildStatusLine(width: number): string {
		const ctx = this.#buildSegmentContext(width);
		const effectiveSettings = this.#resolveSettings();
		const separatorDef = getSeparator(effectiveSettings.separator ?? "powerline-thin", theme);

		const bgAnsi = theme.getBgAnsi("statusLineBg");
		const fgAnsi = theme.getFgAnsi("text");
		const sepAnsi = theme.getFgAnsi("statusLineSep");

		const renderVisibleSegment = (segId: StatusLineSegmentId): string | null => {
			const rendered = renderSegment(segId, ctx);
			return rendered.visible && rendered.content ? rendered.content : null;
		};

		const targetAgentId =
			(this.session as { targetAgentId?: string; currentTargetAgentId?: string }).targetAgentId
			?? (this.session as { currentTargetAgentId?: string }).currentTargetAgentId
			?? "primary";
		const agentIcon = theme.icon.agents ? `${theme.icon.agents} ` : "";
		const agentPart = theme.fg("statusLineSubagents", `${agentIcon}${targetAgentId}`);

		const coreParts = [
			renderVisibleSegment("pi"),
			renderVisibleSegment("permission_mode"),
			agentPart,
			renderVisibleSegment("context_pct"),
			renderVisibleSegment("model"),
		].filter(Boolean) as string[];

		const coreIds = new Set<StatusLineSegmentId>([
			"pi",
			"permission_mode",
			"context_pct",
			"context_total",
			"model",
		]);
		const extraParts: string[] = [];
		for (const segId of [...effectiveSettings.leftSegments, ...effectiveSettings.rightSegments]) {
			if (coreIds.has(segId)) continue;
			const rendered = renderVisibleSegment(segId);
			if (rendered) {
				extraParts.push(rendered);
			}
		}
		const runningBackgroundJobs = this.session.getAsyncJobSnapshot()?.running.length ?? 0;
		if (runningBackgroundJobs > 0) {
			const icon = theme.icon.agents ? `${theme.icon.agents} ` : "";
			const label = `${formatCount("job", runningBackgroundJobs)} running`;
			extraParts.push(theme.fg("statusLineSubagents", `${icon}${label}`));
		}
		const topFillWidth = Math.max(0, width);
		const parts = [...coreParts, ...extraParts];

		const leftSepWidth = visibleWidth(separatorDef.left);
		const leftCapWidth = separatorDef.endCaps ? visibleWidth(separatorDef.endCaps.right) : 0;

		const groupWidth = (parts: string[], capWidth: number, sepWidth: number): number => {
			if (parts.length === 0) return 0;
			const partsWidth = parts.reduce((sum, part) => sum + visibleWidth(part), 0);
			const sepTotal = Math.max(0, parts.length - 1) * (sepWidth + 2);
			return partsWidth + sepTotal + 2 + capWidth;
		};

		let partsWidth = groupWidth(parts, leftCapWidth, leftSepWidth);
		if (topFillWidth > 0) {
			while (partsWidth > topFillWidth && parts.length > coreParts.length) {
				parts.pop();
				partsWidth = groupWidth(parts, leftCapWidth, leftSepWidth);
			}
			while (partsWidth > topFillWidth && parts.length > 1) {
				parts.pop();
				partsWidth = groupWidth(parts, leftCapWidth, leftSepWidth);
			}
		}

		const renderGroup = (parts: string[]): string => {
			if (parts.length === 0) return "";
			const cap = separatorDef.endCaps
				? separatorDef.endCaps.right
				: "";
			const capPrefix = separatorDef.endCaps?.useBgAsFg ? bgAnsi.replace("\x1b[48;", "\x1b[38;") : bgAnsi + sepAnsi;
			const capText = cap ? `${capPrefix}${cap}\x1b[0m` : "";

			let content = bgAnsi + fgAnsi;
			content += ` ${parts.join(` ${sepAnsi}${separatorDef.left}${fgAnsi} `)} `;
			content += "\x1b[0m";

			return capText ? content + capText : content;
		};

		const group = renderGroup(parts);
		if (!group) return "";

		if (topFillWidth === 0) {
			return group;
		}

		partsWidth = groupWidth(parts, leftCapWidth, leftSepWidth);
		const gapWidth = Math.max(0, topFillWidth - partsWidth);
		const gapColor = theme.getFgAnsi("borderMuted");
		const gapFill = `${gapColor}${theme.boxRound.horizontal.repeat(gapWidth)}\x1b[39m`;
		return group + gapFill;
	}

	getTopBorder(width: number): { content: string; width: number } {
		const content = this.#buildStatusLine(width);
		return {
			content,
			width: visibleWidth(content),
		};
	}

	render(width: number): string[] {
		// Only render hook statuses - main status is in editor's top border
		const showHooks = this.#settings.showHookStatus ?? true;
		if (!showHooks || this.#hookStatuses.size === 0) {
			return [];
		}

		const sortedStatuses = Array.from(this.#hookStatuses.entries())
			.sort(([a], [b]) => a.localeCompare(b))
			.map(([, text]) => sanitizeStatusText(text));
		const hookLine = sortedStatuses.join(" ");
		return [truncateToWidth(hookLine, width)];
	}
}
