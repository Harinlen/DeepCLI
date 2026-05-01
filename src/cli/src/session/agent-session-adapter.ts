import { settings } from "@/active-port/coding-agent/config/settings.js";
import { setMustangSessionProvider, type SessionInfo } from "@/active-port/coding-agent/session/session-manager.js";
import type { AgentSessionEvent } from "@/active-port/coding-agent/session/agent-session.js";
import type { AcpClient, SessionUpdateParams } from "@/acp/client.js";
import { ModelService, type ModelProfile } from "@/models/service.js";
import { MustangSession } from "@/session.js";
import { SessionService } from "@/sessions/service.js";
import type { CliSessionInfo } from "@/sessions/types.js";

type Listener = (event: AgentSessionEvent) => void | Promise<void>;

type AssistantMessage = {
	role: "assistant";
	content: Array<{ type: "thinking"; thinking: string } | { type: "text"; text: string } | { type: "toolCall"; id: string; name: string; arguments: Record<string, unknown>; partialJson?: string }>;
	stopReason?: string;
	usage?: Record<string, unknown>;
	timestamp: number;
};

export interface MustangAgentSessionAdapterOptions {
	client: AcpClient;
	session: MustangSession;
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
	readonly modelRegistry = {
		authStorage: undefined,
		isUsingOAuth: () => false,
		getApiKeyForProvider: async () => undefined,
	};
	readonly sessionFile: string | undefined;
	readonly sessionId: string;

	messages: any[] = [];
	state: { messages: any[]; model: { id: string; name: string; provider: string; thinking?: boolean } };
	model: { id: string; name: string; provider: string; thinking?: boolean };
	thinkingLevel = "off";
	isStreaming = false;
	isCompacting = false;
	isGeneratingHandoff = false;
	isBashRunning = false;
	isPythonRunning = false;
	isTtsrAbortPending = false;
	retryAttempt = 0;
	queuedMessageCount = 0;

	#listeners = new Set<Listener>();
	#activeAssistant: AssistantMessage | undefined;
	#toolNames = new Map<string, string>();
	#eventTail: Promise<void> = Promise.resolve();

	constructor(
		private readonly options: MustangAgentSessionAdapterOptions,
		private readonly modelService = new ModelService(options.client),
	) {
		this.sessionId = options.session.sessionId;
		this.sessionManager = new MustangSessionManagerAdapter(options);
		const defaultModel = options.defaultModel || options.modelProfiles?.find(profile => profile.isDefault)?.name || "no-model";
		const profile = options.modelProfiles?.find(item => item.name === defaultModel || item.isDefault);
		this.model = {
			id: profile?.modelId ?? defaultModel,
			name: profile?.name ?? defaultModel,
			provider: profile?.providerType ?? "ACP",
		};
		this.agent = {
			model: this.model,
			state: { messages: this.messages },
			messages: this.messages,
		};
		this.state = { messages: this.messages, model: this.model };
		setMustangSessionProvider({
			listSessions: async (_cwd?: string, limit = 50) => this.listSessionInfos(limit),
		});
	}

	subscribe(listener: Listener): () => void {
		this.#listeners.add(listener);
		return () => this.#listeners.delete(listener);
	}

	on(listener: Listener): () => void {
		return this.subscribe(listener);
	}

	async prompt(text: string, _options: Record<string, unknown> = {}): Promise<unknown> {
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
		this.messages.push(this.#activeAssistant);
		this.#emit({ type: "agent_start" });
		this.#emit({ type: "message_start", message: this.#activeAssistant });

		try {
			const result = await this.options.session.prompt(text, update => this.#handleUpdate(update));
			await this.#flushEvents();
			this.#activeAssistant.stopReason = String((result as { stopReason?: string })?.stopReason ?? "stop");
			this.#emit({ type: "message_end", message: this.#activeAssistant });
			await this.#flushEvents();
			return result;
		} catch (error) {
			if (this.#activeAssistant) {
				await this.#flushEvents();
				this.#activeAssistant.stopReason = "error";
				this.#activeAssistant["errorMessage"] = (error as Error).message;
				this.#emit({ type: "message_end", message: this.#activeAssistant });
				await this.#flushEvents();
			}
			throw error;
		} finally {
			this.isStreaming = false;
			this.#activeAssistant = undefined;
			this.#emit({ type: "agent_end" });
			await this.#flushEvents();
		}
	}

	async executeBash(command: string, onChunk: (chunk: string) => void, options: { excludeFromContext?: boolean } = {}): Promise<{ exitCode: number; cancelled: boolean; output: string }> {
		this.isBashRunning = true;
		let output = "";
		try {
			const result = await this.options.session.executeShell(command, Boolean(options.excludeFromContext), update => {
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
		this.isPythonRunning = true;
		let output = "";
		try {
			const result = await this.options.session.executePython(code, Boolean(options.excludeFromContext), update => {
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
		this.options.session.cancel();
	}

	abortBash(): void {
		this.options.session.cancelExecution("shell");
	}

	abortPython(): void {
		this.options.session.cancelExecution("python");
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
	getToolByName(name: string): Record<string, unknown> { return { name, label: name, status: "pending" }; }
	getTodoPhases(): unknown[] { return []; }
	isFastModeEnabled(): boolean { return false; }
	getAsyncJobSnapshot(): { running: unknown[] } { return { running: [] }; }
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
			provider: profile?.providerType ?? "ACP",
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
		return result === profileName || result === profile.modelId || result === `${profile.providerType}/${profile.modelId}`;
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
		return result.sessionId;
	}

	async loadSession(sessionId: string): Promise<string> {
		const result = await this.options.sessionService.load(sessionId, this.sessionManager.getCwd());
		const summary = "session" in result ? result.session as any : undefined;
		this.options.session = new MustangSession(this.options.sessionService.clientForSession(), result.sessionId, summary);
		this.sessionManager.replaceSession(this.options.session);
		return result.sessionId;
	}

	async switchSession(sessionPath: string): Promise<boolean> {
		await this.loadSession(sessionPath);
		return true;
	}

	async archiveCurrentSession(archived: boolean): Promise<CliSessionInfo> {
		const summary = await this.options.sessionService.archive(this.options.session.sessionId, archived);
		this.options.session.summary = summary;
		this.sessionManager.replaceSession(this.options.session);
		return summary;
	}

	async deleteCurrentSessionAndCreate(): Promise<string> {
		await this.options.sessionService.delete(this.options.session.sessionId, { force: true });
		return this.createSession();
	}

	async deleteSessionByPath(sessionPath: string): Promise<boolean> {
		return this.options.sessionService.delete(sessionPath, { force: true });
	}

	#handleUpdate(update: SessionUpdateParams): void {
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
				break;
			case "session_info_update":
				if (typeof update.title === "string") this.sessionManager.setSessionNameLocal(update.title, "auto");
				break;
		}
	}

	#appendAssistant(kind: "text" | "thinking", text: string): void {
		if (!text || !this.#activeAssistant) return;
		const last = this.#activeAssistant.content[this.#activeAssistant.content.length - 1];
		if (last?.type === kind) {
			if (kind === "text") (last as { text: string }).text += text;
			else (last as { thinking: string }).thinking += text;
		} else if (kind === "text") {
			this.#activeAssistant.content.push({ type: "text", text });
		} else {
			this.#activeAssistant.content.push({ type: "thinking", thinking: text });
		}
		this.#emit({ type: "message_update", message: this.#activeAssistant });
	}

	#startTool(update: SessionUpdateParams): void {
		const toolCallId = String(update.toolCallId ?? update.tool_call_id ?? "");
		if (!toolCallId) return;
		const toolName = String(update.title ?? "tool");
		this.#toolNames.set(toolCallId, toolName);
		const args = parseJsonObject(typeof update.rawInput === "string" ? update.rawInput : typeof update.raw_input === "string" ? update.raw_input : "") ?? {};
		if (this.#activeAssistant) {
			this.#activeAssistant.content.push({ type: "toolCall", id: toolCallId, name: toolName, arguments: args });
			this.#emit({ type: "message_update", message: this.#activeAssistant });
		}
		this.#emit({ type: "tool_execution_start", toolCallId, toolName, args });
	}

	#updateTool(update: SessionUpdateParams, final: boolean): void {
		const toolCallId = String(update.toolCallId ?? update.tool_call_id ?? "");
		if (!toolCallId) return;
		const toolName = this.#toolNames.get(toolCallId) ?? String(update.title ?? "tool");
		const status = String(update.status ?? "");
		const result = { content: normalizeToolContent(update.content, status), details: update.locations ? { locations: update.locations } : undefined };
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
}

export class MustangSessionManagerAdapter {
	titleSource: "auto" | "user" | undefined;
	#session: MustangSession;
	#name: string | undefined;

	constructor(private readonly options: MustangAgentSessionAdapterOptions) {
		this.#session = options.session;
		this.#name = options.session.summary?.title;
		this.titleSource = normalizeTitleSource(options.session.summary?.titleSource);
	}

	replaceSession(session: MustangSession): void {
		this.#session = session;
		this.#name = session.summary?.title;
		this.titleSource = normalizeTitleSource(session.summary?.titleSource);
	}

	getSessionId(): string { return this.#session.sessionId; }
	getSessionFile(): string | undefined { return this.#session.sessionId; }
	getSessionDir(): string { return process.cwd(); }
	getCwd(): string { return this.#session.summary?.cwd || process.cwd(); }
	getSessionName(): string | undefined { return this.#name; }
	getArtifactsDir(): string { return process.cwd(); }
	getLeafId(): string { return this.#session.sessionId; }
	getTree(): unknown { return { id: this.#session.sessionId, children: [] }; }
	getEntries(): unknown[] { return []; }
	getUsageStatistics(): { premiumRequests: number } { return { premiumRequests: 0 }; }
	buildSessionContext(): Record<string, unknown> { return { cwd: this.getCwd(), sessionId: this.#session.sessionId, title: this.#name }; }
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

function normalizeTitleSource(value: unknown): "auto" | "user" | undefined {
	return value === "auto" || value === "user" ? value : undefined;
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
