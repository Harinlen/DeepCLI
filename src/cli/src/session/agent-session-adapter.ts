import { settings } from "@/active-port/coding-agent/config/settings.js";
import { theme } from "@/active-port/coding-agent/modes/theme/theme.js";
import { setMustangSessionProvider, type SessionInfo } from "@/active-port/coding-agent/session/session-manager.js";
import type { AgentSessionEvent } from "@/active-port/coding-agent/session/agent-session.js";
import type { AcpClient, KernelConnectionState, SessionUpdateParams } from "@/acp/client.js";
import { ModelService, type ModelProfile, type ProviderModelItem, type ProviderModelState } from "@/models/service.js";
import { MustangSession, type PermissionMode } from "@/session.js";
import { SessionService } from "@/sessions/service.js";
import type { CliSessionInfo } from "@/sessions/types.js";
import { Box, Text } from "@/tui/index.js";

type Listener = (event: AgentSessionEvent) => void | Promise<void>;

const PERMISSION_MODE_CYCLE: PermissionMode[] = ["default", "accept_edits", "plan", "dont_ask", "auto", "bypass"];

type AssistantMessage = {
	role: "assistant";
	content: Array<{ type: "thinking"; thinking: string } | { type: "text"; text: string } | { type: "toolCall"; id: string; name: string; arguments: Record<string, unknown>; partialJson?: string }>;
	stopReason?: string;
	usage?: Record<string, unknown>;
	duration?: number;
	timestamp: number;
};

export interface MustangAgentSessionAdapterOptions {
	client: AcpClient;
	session?: MustangSession;
	sessionService: SessionService;
	recentSessions?: CliSessionInfo[];
	modelProfiles?: ModelProfile[];
	defaultModel?: string;
}

export class MustangAgentSessionAdapter {
	readonly settings = settings;
	readonly sessionManager: MustangSessionManagerAdapter;
	readonly agent: any;
	readonly customCommands: any[] = [];
	readonly skills: any[] = [];
	readonly configWarnings: string[] = [];
	readonly autoCompactionEnabled = false;
	readonly extensionRunner = undefined;
	readonly providerSessionState = {};
	readonly modelRegistry = {
		authStorage: {
			hasOAuth: (_provider?: string) => false,
			has: (_provider?: string) => false,
			hasAuth: (_provider?: string) => false,
		},
		isUsingOAuth: () => false,
		getApiKeyForProvider: async () => undefined,
	};
	readonly sessionFile: string | undefined;

	messages: any[] = [];
	state: { messages: any[]; model: { id: string; name: string; provider: string; thinking?: boolean; contextWindow?: number | null } };
	model: { id: string; name: string; provider: string; thinking?: boolean; contextWindow?: number | null };
	thinkingLevel = "off";
	isStreaming = false;
	isCompacting = false;
	isGeneratingHandoff = false;
	isBashRunning = false;
	isPythonRunning = false;
	kernelConnectionState: KernelConnectionState = "connected";
	isTtsrAbortPending = false;
	retryAttempt = 0;
	queuedMessageCount = 0;
	currentPermissionMode: PermissionMode = "default";

	#listeners = new Set<Listener>();
	#activeAssistant: AssistantMessage | undefined;
	#activeAssistantSegment: AssistantMessage | undefined;
	#toolNames = new Map<string, string>();
	#eventTail: Promise<void> = Promise.resolve();
	#subagentDepth = 0;

	constructor(
		private readonly options: MustangAgentSessionAdapterOptions,
		private readonly modelService = new ModelService(options.client),
	) {
		this.sessionManager = new MustangSessionManagerAdapter(options);
		const defaultModel = options.defaultModel || options.modelProfiles?.find(profile => profile.isDefault)?.name || "no-model";
		const profile = options.modelProfiles?.find(item => item.name === defaultModel || item.isDefault);
		this.model = {
			id: profile?.modelId ?? defaultModel,
			name: profile?.name ?? defaultModel,
			provider: profile?.providerName ?? "ACP",
			contextWindow: profile?.contextWindow ?? null,
		};
		this.agent = {
			model: this.model,
			state: { messages: this.messages },
			messages: this.messages,
		};
		this.state = { messages: this.messages, model: this.model };
		this.currentPermissionMode = extractPermissionMode(options.session?.summary) ?? "default";
		if (typeof options.client.onUpdate === "function") {
			options.client.onUpdate(update => this.#handleAmbientUpdate(update));
		}
		setMustangSessionProvider({
			listSessions: async (_cwd?: string, limit = 50) => this.listSessionInfos(limit),
		});
	}

	get sessionId(): string {
		return this.options.session?.sessionId ?? "pending";
	}

	subscribe(listener: Listener): () => void {
		this.#listeners.add(listener);
		return () => this.#listeners.delete(listener);
	}

	on(listener: Listener): () => void {
		return this.subscribe(listener);
	}

	async prompt(text: string, _options: Record<string, unknown> = {}): Promise<unknown> {
		const session = await this.#ensureSessionForPrompt();
		const userMessage = {
			role: "user",
			content: [{ type: "text", text }],
			attribution: "user",
			timestamp: Date.now(),
		};
		this.messages.push(userMessage);
		this.#emit({ type: "message_start", message: userMessage });
		this.#emit({ type: "message_end", message: userMessage });

		this.isStreaming = true;
		this.#activeAssistant = { role: "assistant", content: [], timestamp: Date.now() };
		this.#activeAssistantSegment = undefined;
		this.messages.push(this.#activeAssistant);
		this.#emit({ type: "agent_start" });

		try {
			const result = await session.prompt(text, update => this.#handleUpdate(update));
			await this.#flushEvents();
			this.#activeAssistant.stopReason = String((result as { stopReason?: string })?.stopReason ?? "stop");
			this.#endAssistantSegment(this.#activeAssistant.stopReason);
			await this.#flushEvents();
			return result;
		} catch (error) {
			if (this.#activeAssistant) {
				await this.#flushEvents();
				this.#activeAssistant.stopReason = "error";
				this.#activeAssistant["errorMessage"] = (error as Error).message;
				if (!this.#activeAssistantSegment) {
					this.#startAssistantSegment();
				}
				this.#activeAssistantSegment!["errorMessage"] = (error as Error).message;
				this.#endAssistantSegment("error");
				await this.#flushEvents();
			}
			throw error;
		} finally {
			this.isStreaming = false;
			this.#activeAssistant = undefined;
			this.#activeAssistantSegment = undefined;
			this.#emit({ type: "agent_end" });
			await this.#flushEvents();
		}
	}

	getSessionStats(): Record<string, any> {
		let userMessages = 0;
		let assistantMessages = 0;
		let toolCalls = 0;
		let toolResults = 0;
		let inputTokens = this.options.session?.summary?.totalInputTokens ?? 0;
		let outputTokens = this.options.session?.summary?.totalOutputTokens ?? 0;

		for (const message of this.messages) {
			if (message?.role === "user") userMessages++;
			if (message?.role === "assistant") assistantMessages++;
			if (Array.isArray(message?.content)) {
				for (const block of message.content) {
					if (block?.type === "toolCall") toolCalls++;
					if (block?.type === "toolResult") toolResults++;
				}
			}
			const usage = message?.usage;
			if (usage && typeof usage === "object") {
				inputTokens += typeof usage.input === "number" ? usage.input : 0;
				outputTokens += typeof usage.output === "number" ? usage.output : 0;
			}
		}

		return {
			sessionFile: this.sessionFile,
			sessionId: this.sessionId,
			userMessages,
			assistantMessages,
			toolCalls,
			toolResults,
			totalMessages: this.messages.length,
			tokens: {
				input: inputTokens,
				output: outputTokens,
				cacheRead: 0,
				cacheWrite: 0,
				total: inputTokens + outputTokens,
			},
			cost: 0,
			premiumRequests: 0,
		};
	}

	async executeBash(command: string, onChunk: (chunk: string) => void, options: { excludeFromContext?: boolean } = {}): Promise<{ exitCode: number; cancelled: boolean; output: string }> {
		const session = this.#requireSession("Run a chat prompt or /session new before using shell execution.");
		this.isBashRunning = true;
		let output = "";
		try {
			const result = await session.executeShell(command, Boolean(options.excludeFromContext), update => {
				if (update.sessionUpdate !== "execution_update" || update.phase !== "chunk") return;
				const text = String(update.text ?? "");
				output += text;
				onChunk(text);
			});
			return { exitCode: result.exitCode, cancelled: result.cancelled, output };
		} finally {
			this.isBashRunning = false;
		}
	}

	async executePython(code: string, onChunk: (chunk: string) => void, options: { excludeFromContext?: boolean } = {}): Promise<{ exitCode: number; cancelled: boolean; output: string }> {
		const session = this.#requireSession("Run a chat prompt or /session new before using Python execution.");
		this.isPythonRunning = true;
		let output = "";
		try {
			const result = await session.executePython(code, Boolean(options.excludeFromContext), update => {
				if (update.sessionUpdate !== "execution_update" || update.phase !== "chunk") return;
				const text = String(update.text ?? "");
				output += text;
				onChunk(text);
			});
			return { exitCode: result.exitCode, cancelled: result.cancelled, output };
		} finally {
			this.isPythonRunning = false;
		}
	}

	abort(): void {
		this.options.session?.cancel();
	}

	abortBash(): void {
		this.options.session?.cancelExecution("shell");
	}

	abortPython(): void {
		this.options.session?.cancelExecution("python");
	}

	setKernelConnectionState(state: KernelConnectionState): void {
		this.kernelConnectionState = state;
	}

	abortCompaction(): void {}
	abortRetry(): void {}
	dispose(): void {}
	setSlashCommands(_commands: unknown[]): void {}
	setPlanModeState(_state: unknown): void {}
	setPlanReferencePath(_path: string): void {}
	markPlanReferenceSent(): void {}
	async sendPlanModeContext(): Promise<void> {}
	async setActiveToolsByName(_names: string[]): Promise<void> {}
	getActiveToolNames(): string[] { return []; }
	getToolByName(name: string): Record<string, unknown> {
		if (name === "Agent") return agentToolRenderer;
		return { name, label: name, status: "pending" };
	}
	getTodoPhases(): unknown[] { return []; }
	isFastModeEnabled(): boolean { return false; }
	getAsyncJobSnapshot(): { running: unknown[]; recent: unknown[] } { return { running: [], recent: [] }; }
	buildDisplaySessionContext(): unknown { return this.sessionManager.buildSessionContext(); }
	resolveRoleModelWithThinking(): { model?: unknown; thinkingLevel?: string; explicitThinkingLevel?: boolean } { return { model: this.model }; }
	async setModelTemporary(model: any, thinkingLevel?: string): Promise<void> { this.model = model; this.thinkingLevel = thinkingLevel ?? "off"; }
	setThinkingLevel(level?: string): void { this.thinkingLevel = level ?? "off"; }
	cycleThinkingLevel(): undefined { return undefined; }
	async cycleRoleModels(): Promise<undefined> { return undefined; }
	clearQueue(): { steering: unknown[]; followUp: unknown[] } { return { steering: [], followUp: [] }; }
	async promptCustomMessage(message: { content?: string }, options?: Record<string, unknown>): Promise<unknown> {
		return this.prompt(String(message.content ?? ""), options);
	}
	async newSession(): Promise<boolean> {
		const result = await this.options.sessionService.create(process.cwd());
		this.options.session = new MustangSession(this.options.sessionService.clientForSession(), result.sessionId);
		this.sessionManager.replaceSession(this.options.session);
		return true;
	}
	async fork(): Promise<boolean> { return false; }
	async runIdleCompaction(): Promise<void> {}

	async refreshModelProfiles(): Promise<void> {
		const state = await this.modelService.listProfiles();
		const profile = state.profiles.find(item => item.isDefault || item.name === state.defaultModel);
		this.configWarnings.length = 0;
		if (state.profiles.length === 0) {
			this.configWarnings.push("No models available. Use /login or set an API key environment variable, then use /model to select a model.");
		}
		this.model = {
			id: profile?.modelId ?? state.defaultModel ?? "no-model",
			name: profile?.name ?? state.defaultModel ?? "no-model",
			provider: profile?.providerName ?? "ACP",
			contextWindow: profile?.contextWindow ?? null,
		};
		this.agent.model = this.model;
		this.state.model = this.model;
	}

	async setDefaultModelProfile(profileName: string): Promise<boolean> {
		const state = await this.modelService.listProfiles();
		const profile = state.profiles.find(item => item.name === profileName);
		if (!profile) return false;
		const result = await this.modelService.setDefault(profile);
		await this.refreshModelProfiles().catch(() => {});
		return result === profileName || result === profile.modelId || result === `${profile.providerName}/${profile.modelId}`;
	}

	async listProviderModels(): Promise<ProviderModelState> {
		return this.modelService.listProviders();
	}

	async setCurrentModelRole(role: string, provider: string, model: string): Promise<boolean> {
		const result = await this.modelService.setCurrent(role, provider, model);
		await this.refreshModelProfiles().catch(() => {});
		return result.role === role && result.provider === provider && result.model === model;
	}

	async setCurrentModelFromItem(item: ProviderModelItem, role = "default"): Promise<boolean> {
		return this.setCurrentModelRole(role, item.providerName, item.modelId);
	}

	async cyclePermissionMode(): Promise<PermissionMode> {
		const currentIndex = PERMISSION_MODE_CYCLE.indexOf(this.currentPermissionMode);
		const nextMode = PERMISSION_MODE_CYCLE[(currentIndex + 1) % PERMISSION_MODE_CYCLE.length] ?? "default";
		await this.setPermissionMode(nextMode);
		return nextMode;
	}

	async setPermissionMode(mode: PermissionMode): Promise<void> {
		if (this.options.session) {
			await this.options.session.setMode(mode);
		}
		this.currentPermissionMode = mode;
	}

	listSessions(limit = 20): Promise<CliSessionInfo[]> {
		return this.options.sessionService.list({ cwd: this.sessionManager.getCwd(), limit });
	}

	async listSessionInfos(limit = 50): Promise<SessionInfo[]> {
		const sessions = await this.listSessions(limit);
		return sessions.map(cliSessionToOmpSessionInfo);
	}

	async createSession(): Promise<string> {
		const result = await this.options.sessionService.create(this.sessionManager.getCwd());
		this.options.session = new MustangSession(this.options.sessionService.clientForSession(), result.sessionId);
		this.sessionManager.replaceSession(this.options.session);
		if (this.currentPermissionMode !== "default") {
			await this.options.session.setMode(this.currentPermissionMode);
		} else {
			this.#applySessionSetupMode(result);
		}
		return result.sessionId;
	}

	async loadSession(sessionId: string): Promise<string> {
		const result = await this.options.sessionService.load(sessionId, this.sessionManager.getCwd());
		const summary = "session" in result ? result.session as any : undefined;
		this.options.session = new MustangSession(this.options.sessionService.clientForSession(), result.sessionId, summary);
		this.sessionManager.replaceSession(this.options.session);
		this.#applySessionSetupMode(result);
		return result.sessionId;
	}

	async switchSession(sessionPath: string): Promise<boolean> {
		await this.loadSession(sessionPath);
		return true;
	}

	async archiveCurrentSession(archived: boolean): Promise<CliSessionInfo> {
		const session = this.#requireSession("No active session to archive.");
		const summary = await this.options.sessionService.archive(session.sessionId, archived);
		session.summary = summary;
		this.sessionManager.replaceSession(session);
		return summary;
	}

	async deleteCurrentSessionAndCreate(): Promise<string> {
		const session = this.#requireSession("No active session to delete.");
		await this.options.sessionService.delete(session.sessionId, { force: true });
		return this.createSession();
	}

	async deleteSessionByPath(sessionPath: string): Promise<boolean> {
		return this.options.sessionService.delete(sessionPath, { force: true });
	}

	async #ensureSessionForPrompt(): Promise<MustangSession> {
		if (this.options.session) return this.options.session;
		const result = await this.options.sessionService.create(this.sessionManager.getCwd());
		const session = new MustangSession(this.options.sessionService.clientForSession(), result.sessionId);
		this.options.session = session;
		this.sessionManager.replaceSession(session);
		if (this.currentPermissionMode !== "default") {
			await session.setMode(this.currentPermissionMode);
		} else {
			this.#applySessionSetupMode(result);
		}
		return session;
	}

	#requireSession(message: string): MustangSession {
		if (!this.options.session) throw new Error(message);
		return this.options.session;
	}

	#applySessionSetupMode(result: unknown): void {
		this.currentPermissionMode = extractPermissionMode(result) ?? this.currentPermissionMode;
	}

	#handleAmbientUpdate(update: SessionUpdateParams): void {
		if (this.options.session && update.sessionId !== this.options.session.sessionId) return;
		if (update.sessionUpdate !== "current_mode_update") return;
		const mode = parsePermissionMode(update.modeId ?? update.mode_id);
		if (mode) this.currentPermissionMode = mode;
	}

	#handleUpdate(update: SessionUpdateParams): void {
		if (this.#handleSubagentBoundary(update)) return;
		if (this.#subagentDepth > 0 && isSubagentPrivateUpdate(update)) return;
		switch (update.sessionUpdate) {
			case "agent_message_chunk":
				this.#appendAssistant("text", extractText(update.content));
				break;
			case "agent_thought_chunk":
				this.#appendAssistant("thinking", extractText(update.content));
				break;
			case "tool_call":
				this.#startTool(update);
				break;
			case "tool_call_update":
				this.#updateTool(update, false);
				break;
			case "current_mode_update":
				this.#handleAmbientUpdate(update);
				break;
			case "session_info_update":
				if (typeof update.title === "string") this.sessionManager.setSessionNameLocal(update.title, "auto");
				break;
			case "usage_update":
				this.#applyUsageUpdate(update);
				break;
		}
	}

	#handleSubagentBoundary(update: SessionUpdateParams): boolean {
		if (update.sessionUpdate !== "tool_call_update") return false;
		const meta = readUpdateMeta(update);
		if (meta?.["mustang.agent/agentStart"]) {
			this.#subagentDepth += 1;
			return true;
		}
		if (meta?.["mustang.agent/agentEnd"]) {
			this.#subagentDepth = Math.max(0, this.#subagentDepth - 1);
			return true;
		}
		return false;
	}

	#applyUsageUpdate(update: SessionUpdateParams): void {
		if (!this.#activeAssistant) return;
		const input = numberFromUpdate(update.inputTokens ?? update.input_tokens);
		const output = numberFromUpdate(update.outputTokens ?? update.output_tokens);
		const cacheRead = numberFromUpdate(update.cacheReadTokens ?? update.cache_read_tokens);
		const cacheWrite = numberFromUpdate(update.cacheWriteTokens ?? update.cache_write_tokens);
		this.#activeAssistant.usage = { input, output, cacheRead, cacheWrite };
		const duration = numberFromUpdate(update.durationMs ?? update.duration_ms);
		if (duration > 0) {
			this.#activeAssistant.duration = duration;
		}
	}

	#appendAssistant(kind: "text" | "thinking", text: string): void {
		if (!text || !this.#activeAssistant) return;
		appendAssistantContent(this.#activeAssistant, kind, text);
		const segment = this.#activeAssistantSegment ?? this.#startAssistantSegment();
		appendAssistantContent(segment, kind, text);
		this.#emit({ type: "message_update", message: segment });
	}

	#startTool(update: SessionUpdateParams): void {
		const toolCallId = String(update.toolCallId ?? update.tool_call_id ?? "");
		if (!toolCallId) return;
		const toolName = String(update.title ?? "tool");
		this.#toolNames.set(toolCallId, toolName);
		const args = parseJsonObject(typeof update.rawInput === "string" ? update.rawInput : typeof update.raw_input === "string" ? update.raw_input : "") ?? {};
		if (this.#activeAssistant) {
			this.#activeAssistant.content.push({ type: "toolCall", id: toolCallId, name: toolName, arguments: args });
		}
		this.#endAssistantSegment("tool_use");
		this.#emit({ type: "tool_execution_start", toolCallId, toolName, args });
	}

	#updateTool(update: SessionUpdateParams, final: boolean): void {
		const toolCallId = String(update.toolCallId ?? update.tool_call_id ?? "");
		if (!toolCallId) return;
		const toolName = this.#toolNames.get(toolCallId) ?? String(update.title ?? "tool");
		const status = String(update.status ?? "");
		const meta = readUpdateMeta(update);
		const details = buildToolDetails(update, meta);
		const result = { content: normalizeToolContent(update.content, status), details };
		if (final || status === "completed" || status === "failed" || status === "error") {
			this.#emit({ type: "tool_execution_end", toolCallId, toolName, result, isError: status === "failed" || status === "error" });
		} else {
			this.#emit({ type: "tool_execution_update", toolCallId, toolName, partialResult: result });
		}
	}

	#emit(event: AgentSessionEvent): void {
		this.#eventTail = this.#eventTail
			.then(async () => {
				for (const listener of this.#listeners) {
					await listener(event);
				}
			})
			.catch(error => {
				console.error("[cli] session event handler failed:", error);
			});
	}

	async #flushEvents(): Promise<void> {
		await this.#eventTail;
	}

	#startAssistantSegment(): AssistantMessage {
		const segment: AssistantMessage = { role: "assistant", content: [], timestamp: Date.now() };
		this.#activeAssistantSegment = segment;
		this.#emit({ type: "message_start", message: segment });
		return segment;
	}

	#endAssistantSegment(stopReason: string): void {
		const segment = this.#activeAssistantSegment;
		if (!segment) return;
		segment.stopReason = stopReason;
		segment.usage = this.#activeAssistant?.usage;
		this.#emit({ type: "message_end", message: segment });
		this.#activeAssistantSegment = undefined;
	}
}

export class MustangSessionManagerAdapter {
	titleSource: "auto" | "user" | undefined;
	#session: MustangSession | undefined;
	#name: string | undefined;

	constructor(private readonly options: MustangAgentSessionAdapterOptions) {
		this.#session = options.session;
		this.#name = options.session?.summary?.title;
		this.titleSource = normalizeTitleSource(options.session?.summary?.titleSource);
	}

	replaceSession(session: MustangSession): void {
		this.#session = session;
		this.#name = session.summary?.title;
		this.titleSource = normalizeTitleSource(session.summary?.titleSource);
	}

	getSessionId(): string { return this.#session?.sessionId ?? "pending"; }
	getSessionFile(): string | undefined { return this.#session?.sessionId; }
	getSessionDir(): string { return process.cwd(); }
	getCwd(): string { return this.#session?.summary?.cwd || process.cwd(); }
	getSessionName(): string | undefined { return this.#name; }
	getArtifactsDir(): string { return process.cwd(); }
	getLeafId(): string { return this.getSessionId(); }
	getTree(): unknown { return { id: this.getSessionId(), children: [] }; }
	getEntries(): unknown[] { return []; }
	getUsageStatistics(): { premiumRequests: number } { return { premiumRequests: 0 }; }
	buildSessionContext(): Record<string, unknown> { return { cwd: this.getCwd(), sessionId: this.getSessionId(), title: this.#name }; }
	async flush(): Promise<void> {}
	async moveTo(_path: string): Promise<void> {}
	appendModeChange(_mode: string, _meta?: unknown): void {}
	appendLabelChange(_id: string, _label: string): void {}
	async setSessionName(title: string, source: "auto" | "user" = "user"): Promise<boolean> {
		const next = title.trim();
		if (!next) return false;
		this.#name = next;
		this.titleSource = source;
		try {
			if (!this.#session) return true;
			const summary = await this.options.sessionService.rename(this.#session.sessionId, next);
			this.#session.summary = summary;
		} catch {
			// Keep the UI responsive even if the kernel rejects an opportunistic title update.
		}
		return true;
	}
	setSessionNameLocal(title: string, source: "auto" | "user" = "auto"): void {
		this.#name = title;
		this.titleSource = source;
	}
}

const agentToolRenderer = {
	name: "Agent",
	label: "Agent",
	status: "pending",
	mergeCallAndResult: true,
	renderCall(args: unknown, state: { isPartial?: boolean; expanded?: boolean }) {
		const input = args && typeof args === "object" ? args as Record<string, unknown> : {};
		const description = typeof input.description === "string" && input.description.trim()
			? input.description.trim()
			: typeof input.prompt === "string" && input.prompt.trim()
				? input.prompt.trim()
				: "sub-agent";
		const box = new Box(0, 0);
		if (state.isPartial) {
			box.addChild(new Text(`${theme.fg("toolTitle", "running Agent")}\n ${theme.fg("dim", theme.tree.last)} ${theme.fg("dim", `description="${truncatePlain(description, 48)}"`)}`, 0, 0));
		} else {
			box.addChild(new Text(theme.fg("toolTitle", `Agent(${truncatePlain(description, 48)})`), 0, 0));
		}
		return box;
	},
	renderResult(result: { content?: Array<{ type: string; text?: string }>; details?: Record<string, unknown>; isError?: boolean }, state: { expanded?: boolean }) {
		const text = (result.content ?? [])
			.filter(block => block.type === "text")
			.map(block => block.text ?? "")
			.join("\n")
			.trim();
		const box = new Box(0, 0);
		if (result.isError) {
			box.addChild(new Text(theme.fg("error", text || "Agent failed"), 0, 0));
			return box;
		}
		if (!state.expanded) {
			const summary = formatAgentDoneSummary(readAgentStatsFromDetails(result.details));
			box.addChild(new Text(` ${theme.fg("dim", theme.tree.last)} ${theme.fg("dim", `${summary} (ctrl+o to expand)`)}`, 0, 0));
			return box;
		}
		box.addChild(new Text(text || theme.fg("dim", "(no output)"), 0, 0));
		return box;
	},
};

function readUpdateMeta(update: SessionUpdateParams): Record<string, unknown> | undefined {
	const meta = update._meta ?? update.meta;
	return meta && typeof meta === "object" && !Array.isArray(meta) ? meta as Record<string, unknown> : undefined;
}

function buildToolDetails(update: SessionUpdateParams, meta: Record<string, unknown> | undefined): Record<string, unknown> | undefined {
	const details: Record<string, unknown> = {};
	if (update.locations) details.locations = update.locations;
	if (meta) {
		details.meta = meta;
		const agent = readAgentStats(meta);
		if (agent) details.agent = agent;
	}
	return Object.keys(details).length > 0 ? details : undefined;
}

type AgentStats = {
	toolUseCount: number;
	inputTokens: number;
	outputTokens: number;
	totalTokens: number;
	durationMs: number;
};

function readAgentStats(meta: Record<string, unknown> | undefined): AgentStats | undefined {
	const raw = meta?.["mustang.agent/agentStats"];
	if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
	const source = raw as Record<string, unknown>;
	const inputTokens = numberFromUpdate(source.inputTokens ?? source.input_tokens);
	const outputTokens = numberFromUpdate(source.outputTokens ?? source.output_tokens);
	const totalTokens = numberFromUpdate(source.totalTokens ?? source.total_tokens) || inputTokens + outputTokens;
	return {
		toolUseCount: numberFromUpdate(source.toolUseCount ?? source.tool_use_count),
		inputTokens,
		outputTokens,
		totalTokens,
		durationMs: numberFromUpdate(source.durationMs ?? source.duration_ms),
	};
}

function readAgentStatsFromDetails(details: Record<string, unknown> | undefined): AgentStats | undefined {
	const raw = details?.agent;
	return raw && typeof raw === "object" && !Array.isArray(raw) ? raw as AgentStats : undefined;
}

function formatAgentDoneSummary(stats: AgentStats | undefined): string {
	if (!stats) return "Done";
	return `Done (${stats.toolUseCount} ${stats.toolUseCount === 1 ? "tool use" : "tool uses"} · ${formatTokenCount(stats.totalTokens)} tokens · ${formatDuration(stats.durationMs)})`;
}

function formatTokenCount(value: number): string {
	if (value >= 1_000_000) return `${trimFixed(value / 1_000_000, 1)}m`;
	if (value >= 1_000) return `${trimFixed(value / 1_000, 1)}k`;
	return `${Math.max(0, Math.round(value))}`;
}

function formatDuration(ms: number): string {
	if (ms >= 1000) return `${Math.max(1, Math.round(ms / 1000))}s`;
	return `${Math.max(0, Math.round(ms))}ms`;
}

function trimFixed(value: number, digits: number): string {
	return value.toFixed(digits).replace(/\.0$/, "");
}

function isSubagentPrivateUpdate(update: SessionUpdateParams): boolean {
	return update.sessionUpdate === "agent_message_chunk" ||
		update.sessionUpdate === "agent_thought_chunk" ||
		update.sessionUpdate === "tool_call" ||
		update.sessionUpdate === "tool_call_update";
}

function truncatePlain(value: string, max: number): string {
	return value.length <= max ? value : `${value.slice(0, Math.max(0, max - 1))}…`;
}

function extractText(content: unknown): string {
	const block = content as { text?: unknown } | undefined;
	return typeof block?.text === "string" ? block.text : "";
}

function parseJsonObject(value: string): Record<string, unknown> | null {
	if (!value.trim()) return null;
	try {
		const parsed = JSON.parse(value) as unknown;
		return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
	} catch {
		return null;
	}
}

function appendAssistantContent(message: AssistantMessage, kind: "text" | "thinking", text: string): void {
	const last = message.content[message.content.length - 1];
	if (last?.type === kind) {
		if (kind === "text") (last as { text: string }).text += text;
		else (last as { thinking: string }).thinking += text;
	} else if (kind === "text") {
		message.content.push({ type: "text", text });
	} else {
		message.content.push({ type: "thinking", thinking: text });
	}
}

function normalizeToolContent(content: unknown, status: string): Array<{ type: string; text?: string; data?: string; mimeType?: string }> {
	if (Array.isArray(content)) {
		return content.map((block) => {
			const item = block as { type?: string; text?: string; data?: string; mimeType?: string };
			return { type: item.type ?? "text", text: item.text, data: item.data, mimeType: item.mimeType };
		});
	}
	if (typeof content === "string") return [{ type: "text", text: content }];
	if (status === "in_progress") return [{ type: "text", text: "Running..." }];
	return status ? [{ type: "text", text: status }] : [];
}

function numberFromUpdate(value: unknown): number {
	return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function normalizeTitleSource(value: unknown): "auto" | "user" | undefined {
	return value === "auto" || value === "user" ? value : undefined;
}

function parsePermissionMode(value: unknown): PermissionMode | undefined {
	return typeof value === "string" && (PERMISSION_MODE_CYCLE as string[]).includes(value)
		? value as PermissionMode
		: undefined;
}

function extractPermissionMode(value: unknown): PermissionMode | undefined {
	const item = value as { modes?: unknown; configOptions?: unknown; raw?: unknown } | undefined;
	const modes = item?.modes as { currentModeId?: unknown; current_mode_id?: unknown } | undefined;
	const fromModes = parsePermissionMode(modes?.currentModeId ?? modes?.current_mode_id);
	if (fromModes) return fromModes;

	const configOptions = Array.isArray(item?.configOptions) ? item.configOptions : undefined;
	const modeConfig = configOptions?.find(option => {
		const record = option as { configId?: unknown; config_id?: unknown } | undefined;
		return record?.configId === "mode" || record?.config_id === "mode";
	}) as { currentValue?: unknown; current_value?: unknown } | undefined;
	const fromConfig = parsePermissionMode(modeConfig?.currentValue ?? modeConfig?.current_value);
	if (fromConfig) return fromConfig;

	return item?.raw ? extractPermissionMode(item.raw) : undefined;
}

function cliSessionToOmpSessionInfo(session: CliSessionInfo): SessionInfo {
	const created = parseDate(session.createdAt) ?? parseDate(session.updatedAt) ?? new Date(0);
	const modified = parseDate(session.updatedAt) ?? created;
	const title = session.title?.trim() || undefined;
	const firstMessage = title || session.sessionId;
	return {
		path: session.path || session.sessionId,
		id: session.sessionId,
		cwd: session.cwd || process.cwd(),
		title,
		created,
		modified,
		messageCount: 0,
		firstMessage,
		allMessagesText: `${title ?? ""} ${session.cwd ?? ""} ${session.sessionId}`.trim(),
	};
}

function parseDate(value: string | null | undefined): Date | undefined {
	if (!value) return undefined;
	const date = new Date(value);
	return Number.isNaN(date.getTime()) ? undefined : date;
}
