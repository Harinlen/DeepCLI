import { settings } from "@/active-port/coding-agent/config/settings.js";
import { theme } from "@/active-port/coding-agent/modes/theme/theme.js";
import { setMustangSessionProvider, type SessionInfo } from "@/active-port/coding-agent/session/session-manager.js";
import type { AgentSessionEvent } from "@/active-port/coding-agent/session/agent-session.js";
import type { AcpClient, KernelConnectionState, SessionUpdateParams } from "@/acp/client.js";
import { ModelService, type ModelAddInput, type ModelProfile, type ModelUpdateInput, type ProviderModelItem, type ProviderModelState } from "@/models/service.js";
import { MustangSession, type CostUsageReport, type PermissionMode } from "@/session.js";
import { SessionService } from "@/sessions/service.js";
import type { CliSessionInfo } from "@/sessions/types.js";
import { Box, Text } from "@/tui/index.js";
import {
	WebFetchService,
	type SetWebFetchBackendResult,
	type WebBridgeStatus,
	type WebFetchBackendState,
	type WebFetchConfigState,
} from "@/webfetch/service.js";

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

type ContextUsageSnapshot = {
	totalTokens: number;
	contextWindow: number | null;
	percent: number;
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
	targetAgentId?: string;
	targetAgentSessionId?: string;

	#listeners = new Set<Listener>();
	#activeAssistant: AssistantMessage | undefined;
	#activeAssistantSegment: AssistantMessage | undefined;
	#toolNames = new Map<string, string>();
	#skillCommandNames = new Set<string>();
	#eventTail: Promise<void> = Promise.resolve();
	#subagentDepth = 0;

	constructor(
		private readonly options: MustangAgentSessionAdapterOptions,
		private readonly modelService = new ModelService(options.client),
		private readonly webFetchService = new WebFetchService(options.client),
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
			listSessions: async (cwd?: string, limit = 50) => this.listSessionInfos(limit, cwd),
			listRecentSessions: async (cwd?: string, limit = 5) => this.listRecentSessionInfos(limit, cwd),
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
		session.targetAgentId = this.targetAgentId;
		session.targetAgentSessionId = this.targetAgentSessionId;
		const skillInvocation = this.#parseSkillInvocation(text);
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
			const result = skillInvocation
				? await session.activateSkill(
					skillInvocation.name,
					skillInvocation.args,
					update => this.#handleUpdate(update),
					{ mode: this.currentPermissionMode },
				)
				: await session.prompt(text, update => this.#handleUpdate(update), {
					mode: this.currentPermissionMode,
				});
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

	async fetchCostReport(): Promise<CostUsageReport> {
		const session = await this.#ensureSessionForPrompt();
		const report = await session.getUsage();
		const contextWindow = this.model.contextWindow;
		if ((!report.context.contextWindow || report.context.contextWindow <= 0) && contextWindow && contextWindow > 0) {
			report.context.contextWindow = contextWindow;
			report.context.percent = report.context.totalTokens > 0
				? Number(((report.context.totalTokens / contextWindow) * 100).toFixed(1))
				: 0;
		}
		this.sessionManager.recordContextUsage({
			totalTokens: report.context.totalTokens,
			contextWindow: report.context.contextWindow ?? null,
			percent: report.context.percent,
		});
		return report;
	}

	async executeBash(command: string, onChunk: (chunk: string) => void, options: { excludeFromContext?: boolean } = {}): Promise<{ exitCode: number; cancelled: boolean; output: string }> {
		const session = await this.#ensureSessionForPrompt();
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
		const session = await this.#ensureSessionForPrompt();
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
		const cwd = process.cwd();
		const result = await this.options.sessionService.create(cwd);
		this.options.session = new MustangSession(
			this.options.sessionService.clientForSession(),
			result.sessionId,
			createdSessionSummary(result.sessionId, cwd),
		);
		this.options.session.targetAgentId = this.targetAgentId;
		this.options.session.targetAgentSessionId = this.targetAgentSessionId;
		this.sessionManager.replaceSession(this.options.session);
		this.#resetTranscript();
		return true;
	}
	async fork(): Promise<boolean> { return false; }
	async runIdleCompaction(): Promise<void> {}

	async refreshModelProfiles(): Promise<void> {
		const state = await this.modelService.listProfiles();
		const profile = state.profiles.find(item => item.isDefault || item.name === state.defaultModel);
		let providerModel: ProviderModelItem | undefined;
		if (!profile || !profile.contextWindow) {
			const providerState = await this.modelService.listProviders().catch(() => undefined);
			const currentDefault = providerState?.currentUsed?.default;
			providerModel = currentDefault
				? providerState?.models.find(item => item.providerName === currentDefault[0] && item.modelId === currentDefault[1])
				: providerState?.models.find(item => item.roles.includes("default")) ?? providerState?.models[0];
		}
		this.configWarnings.length = 0;
		if (state.profiles.length === 0 && !providerModel) {
			this.configWarnings.push("No models available. Use /model add to add a model.");
		}
		this.model = {
			id: profile?.modelId ?? providerModel?.modelId ?? state.defaultModel ?? "no-model",
			name: profile?.name ?? providerModel?.displayName ?? state.defaultModel ?? "no-model",
			provider: profile?.providerName ?? providerModel?.providerName ?? "ACP",
			contextWindow: profile?.contextWindow ?? providerModel?.contextWindow ?? null,
		};
		this.agent.model = this.model;
		this.state.model = this.model;
	}

	async refreshCommandCatalog(): Promise<void> {
		const session = this.options.session ?? new MustangSession(this.options.sessionService.clientForSession(), "catalog-probe");
		const commands = await session.listCommands();
		this.customCommands.length = 0;
		this.#skillCommandNames.clear();
		for (const command of commands) {
			if (command.source !== "skill") continue;
			this.#skillCommandNames.add(command.name);
			for (const alias of command.aliases ?? []) {
				this.#skillCommandNames.add(alias);
			}
			this.customCommands.push({
				source: "skill",
				command: {
					name: command.name,
					description: command.description,
					usage: command.usage,
				},
			});
		}
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

	async runtimeStatus(): Promise<unknown> {
		const session = this.options.session ?? new MustangSession(this.options.sessionService.clientForSession(), "runtime-probe");
		return session.runtimeStatus();
	}

	async runtimeRestart(reason?: string): Promise<unknown> {
		const session = this.options.session ?? new MustangSession(this.options.sessionService.clientForSession(), "runtime-probe");
		return session.runtimeRestart(reason);
	}

	async managementRequest<R = unknown>(
		method: string,
		params: Record<string, unknown> = {},
	): Promise<R> {
		const session = this.options.session ?? new MustangSession(this.options.sessionService.clientForSession(), "management-probe");
		return session.managementRequest<R>(method, params);
	}

	async setCurrentModelRole(role: string, provider: string, model: string): Promise<boolean> {
		const result = await this.modelService.setCurrent(role, provider, model);
		await this.refreshModelProfiles().catch(() => {});
		return result.role === role && result.provider === provider && result.model === model;
	}

	async listWebFetchBackends(): Promise<WebFetchBackendState> {
		return this.webFetchService.backendOptions();
	}

	async setWebFetchBackend(backend: string, runSetup = false, apiKey?: string): Promise<SetWebFetchBackendResult> {
		return this.webFetchService.setBackend(backend, runSetup, apiKey);
	}

	async getWebFetchConfig(): Promise<WebFetchConfigState> {
		return this.webFetchService.getConfig();
	}

	async setWebFetchConfig(path: string, value: unknown): Promise<WebFetchConfigState> {
		return this.webFetchService.setConfig(path, value);
	}

	async webBridgeStatus(includePairingToken = false): Promise<WebBridgeStatus> {
		return this.webFetchService.webBridgeStatus(includePairingToken);
	}

	async webBridgePairStart(): Promise<WebBridgeStatus> {
		return this.webFetchService.webBridgePairStart();
	}

	async webBridgePairReset(): Promise<WebBridgeStatus> {
		return this.webFetchService.webBridgePairReset();
	}

	async setCurrentModelFromItem(item: ProviderModelItem, role = "default"): Promise<boolean> {
		return this.setCurrentModelRole(role, item.providerName, item.modelId);
	}

	async updateProviderModel(input: ModelUpdateInput): Promise<ProviderModelItem> {
		const result = await this.modelService.updateModel(input);
		await this.refreshModelProfiles().catch(() => {});
		return result;
	}

	async addProviderModel(input: ModelAddInput): Promise<ProviderModelItem> {
		const result = await this.modelService.addModel(input);
		await this.refreshModelProfiles().catch(() => {});
		return result;
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
		this.#emit({ type: "current_mode_update", mode });
	}

	listSessions(limit = 20, cwd?: string): Promise<CliSessionInfo[]> {
		return this.options.sessionService.list({ cwd: cwd ?? this.sessionManager.getCwd(), limit });
	}

	async listSessionInfos(limit = 50, cwd?: string): Promise<SessionInfo[]> {
		const sessions = await this.listSessions(limit, cwd);
		return sessions.map(cliSessionToOmpSessionInfo);
	}

	async listRecentSessionInfos(limit = 5, cwd?: string): Promise<SessionInfo[]> {
		const sessions = await this.listSessions(limit, cwd);
		const active = currentSessionInfo(this.options.session, this.sessionManager);
		if (!active || (cwd && active.cwd !== cwd)) {
			return sessions.map(cliSessionToOmpSessionInfo);
		}
		const withoutActive = sessions.filter(session => session.sessionId !== active.id);
		return [active, ...withoutActive.map(cliSessionToOmpSessionInfo)].slice(0, limit);
	}

	async createSession(): Promise<string> {
		const cwd = this.sessionManager.getCwd();
		const result = await this.options.sessionService.create(cwd);
		this.options.session = new MustangSession(
			this.options.sessionService.clientForSession(),
			result.sessionId,
			createdSessionSummary(result.sessionId, cwd),
		);
		this.sessionManager.replaceSession(this.options.session);
		this.#resetTranscript();
		if (this.currentPermissionMode !== "default") {
			await this.options.session.setMode(this.currentPermissionMode);
		} else {
			this.#applySessionSetupMode(result);
		}
		return result.sessionId;
	}

	async loadSession(sessionId: string): Promise<string> {
		this.#beginReplay();
		let replayUpdateCount = 0;
		let lastReplayUpdateAt = Date.now();
		const unsubscribe = this.options.client.onUpdate(update => {
			replayUpdateCount += 1;
			lastReplayUpdateAt = Date.now();
			this.#handleReplayUpdate(update);
		});
		let result: any;
		try {
			result = await this.options.sessionService.load(sessionId, this.sessionManager.getCwd());
			await waitForReplayQuiet(() => ({ count: replayUpdateCount, updatedAt: lastReplayUpdateAt }));
			this.#finishReplayAssistant("stop");
			await this.#flushEvents();
		} catch (error) {
			this.#finishReplayAssistant("error");
			await this.#flushEvents();
			throw error;
		} finally {
			unsubscribe();
		}
		const loadedSessionId = result.sessionId ?? result.session?.sessionId ?? result.session?.id ?? sessionId;
		const summary = "session" in result ? result.session as any : undefined;
		const replayedContextUsage = this.sessionManager.getContextUsage();
		this.options.session = new MustangSession(this.options.sessionService.clientForSession(), loadedSessionId, summary);
		this.sessionManager.replaceSession(this.options.session);
		this.sessionManager.recordContextUsage(replayedContextUsage);
		this.#applySessionSetupMode(result);
		return loadedSessionId;
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
		const cwd = this.sessionManager.getCwd();
		const result = await this.options.sessionService.create(cwd);
		const session = new MustangSession(
			this.options.sessionService.clientForSession(),
			result.sessionId,
			createdSessionSummary(result.sessionId, cwd),
		);
		session.targetAgentId = this.targetAgentId;
		session.targetAgentSessionId = this.targetAgentSessionId;
		this.options.session = session;
		this.sessionManager.replaceSession(session);
		this.#resetTranscript();
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

	#parseSkillInvocation(text: string): { name: string; args: string } | undefined {
		const trimmed = text.trimStart();
		if (!trimmed.startsWith("/") || trimmed.startsWith("//")) return undefined;
		const match = /^\/([^\s/]+)(?:\s+([\s\S]*))?$/.exec(trimmed);
		if (!match) return undefined;
		const name = match[1] ?? "";
		if (!this.#skillCommandNames.has(name)) return undefined;
		const skillName = name.startsWith("skill:") ? name.slice("skill:".length) : name;
		return { name: skillName, args: match[2] ?? "" };
	}

	#applySessionSetupMode(result: unknown): void {
		this.currentPermissionMode = extractPermissionMode(result) ?? this.currentPermissionMode;
	}

	#handleAmbientUpdate(update: SessionUpdateParams): void {
		if (this.options.session && update.sessionId !== this.options.session.sessionId) return;
		if (update.sessionUpdate !== "current_mode_update") return;
		const mode = parsePermissionMode(update.modeId ?? update.mode_id);
		if (mode) {
			this.currentPermissionMode = mode;
			this.#emit({ type: "current_mode_update", mode });
		}
	}

	#beginReplay(): void {
		this.messages.length = 0;
		this.#activeAssistant = undefined;
		this.#activeAssistantSegment = undefined;
		this.#toolNames.clear();
		this.#subagentDepth = 0;
	}

	#handleReplayUpdate(update: SessionUpdateParams): void {
		if (this.#handleSubagentBoundary(update)) return;
		if (this.#subagentDepth > 0 && isSubagentPrivateUpdate(update)) return;
		switch (update.sessionUpdate) {
			case "user_message_chunk":
				this.#finishReplayAssistant("stop");
				this.#appendReplayedUser(extractText(update.content));
				break;
			case "agent_message_chunk":
				this.#ensureReplayAssistant();
				this.#appendAssistant("text", extractText(update.content));
				break;
			case "agent_thought_chunk":
				this.#ensureReplayAssistant();
				this.#appendAssistant("thinking", extractText(update.content));
				break;
			case "tool_call":
				this.#ensureReplayAssistant();
				this.#startTool(update);
				break;
			case "tool_call_update":
				this.#updateTool(update, false);
				break;
			case "current_mode_update":
				this.#handleReplayMode(update);
				break;
			case "session_info_update":
				if (typeof update.title === "string") this.sessionManager.setSessionNameLocal(update.title, "auto");
				break;
			case "usage_update":
				this.#applyUsageUpdate(update);
				break;
		}
	}

	#appendReplayedUser(text: string): void {
		if (!text) return;
		const userMessage = {
			role: "user",
			content: [{ type: "text", text }],
			attribution: "user",
			timestamp: Date.now(),
		};
		this.messages.push(userMessage);
		this.#emit({ type: "message_start", message: userMessage });
		this.#emit({ type: "message_end", message: userMessage });
	}

	#ensureReplayAssistant(): void {
		if (this.#activeAssistant) return;
		this.#activeAssistant = { role: "assistant", content: [], timestamp: Date.now() };
		this.messages.push(this.#activeAssistant);
	}

	#finishReplayAssistant(stopReason: string): void {
		if (!this.#activeAssistant) return;
		this.#activeAssistant.stopReason = stopReason;
		this.#endAssistantSegment(stopReason);
		this.#activeAssistant = undefined;
	}

	#handleReplayMode(update: SessionUpdateParams): void {
		const mode = parsePermissionMode(update.modeId ?? update.mode_id);
		if (mode) {
			this.currentPermissionMode = mode;
			this.#emit({ type: "current_mode_update", mode });
		}
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
		const input = numberFromUpdate(update.inputTokens ?? update.input_tokens);
		const output = numberFromUpdate(update.outputTokens ?? update.output_tokens);
		const cacheRead = numberFromUpdate(update.cacheReadTokens ?? update.cache_read_tokens);
		const cacheWrite = numberFromUpdate(update.cacheWriteTokens ?? update.cache_write_tokens);
		this.sessionManager.recordContextUsage(contextUsageSnapshotFromUpdate(update, this.model.contextWindow));
		if (!this.#activeAssistant) return;
		this.#activeAssistant.usage = { input, output, cacheRead, cacheWrite };
		const duration = numberFromUpdate(update.durationMs ?? update.duration_ms);
		if (duration > 0) {
			this.#activeAssistant.duration = duration;
		}
		this.sessionManager.recordUsage(input, output);
	}

	#resetTranscript(): void {
		this.messages = [];
		this.agent.messages = this.messages;
		this.agent.state.messages = this.messages;
		this.state.messages = this.messages;
		this.#activeAssistant = undefined;
		this.#activeAssistantSegment = undefined;
		this.#toolNames.clear();
		this.#subagentDepth = 0;
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
		const meta = readUpdateMeta(update);
		const details = buildToolDetails(update, meta);
		if (this.#activeAssistant) {
			this.#activeAssistant.content.push({ type: "toolCall", id: toolCallId, name: toolName, arguments: args });
		}
		this.#endAssistantSegment("tool_use");
		this.#emit({ type: "tool_execution_start", toolCallId, toolName, args, details });
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
		segment.duration = this.#activeAssistant?.duration;
		this.#emit({ type: "message_end", message: segment });
		this.#activeAssistantSegment = undefined;
	}
}

async function waitForReplayQuiet(
	snapshot: () => { count: number; updatedAt: number },
	options: { quietMs?: number; timeoutMs?: number } = {},
): Promise<void> {
	const quietMs = options.quietMs ?? 200;
	const timeoutMs = options.timeoutMs ?? 3000;
	const startedAt = Date.now();
	while (Date.now() - startedAt < timeoutMs) {
		const current = snapshot();
		if (current.count > 0 && Date.now() - current.updatedAt >= quietMs) return;
		await sleep(25);
	}
}

function sleep(ms: number): Promise<void> {
	return new Promise(resolve => setTimeout(resolve, ms));
}

export class MustangSessionManagerAdapter {
	titleSource: "auto" | "user" | undefined;
	#session: MustangSession | undefined;
	#name: string | undefined;
	#liveInputTokens = 0;
	#liveOutputTokens = 0;
	#contextUsage: ContextUsageSnapshot = {
		totalTokens: 0,
		contextWindow: null,
		percent: 0,
	};

	constructor(private readonly options: MustangAgentSessionAdapterOptions) {
		this.#session = options.session;
		this.#name = options.session?.summary?.title;
		this.titleSource = normalizeTitleSource(options.session?.summary?.titleSource);
	}

	replaceSession(session: MustangSession): void {
		this.#session = session;
		this.#name = session.summary?.title;
		this.titleSource = normalizeTitleSource(session.summary?.titleSource);
		this.#liveInputTokens = 0;
		this.#liveOutputTokens = 0;
		this.#contextUsage = {
			totalTokens: 0,
			contextWindow: null,
			percent: 0,
		};
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
	recordUsage(input: number, output: number): void {
		this.#liveInputTokens += input;
		this.#liveOutputTokens += output;
	}
	recordContextUsage(snapshot: ContextUsageSnapshot): void {
		this.#contextUsage = snapshot;
	}
	getContextUsage(): ContextUsageSnapshot {
		return this.#contextUsage;
	}
	getUsageStatistics(): { input: number; output: number; cacheRead: number; cacheWrite: number; cost: number; premiumRequests: number } {
		const input = (this.#session?.summary?.totalInputTokens ?? 0) + this.#liveInputTokens;
		const output = (this.#session?.summary?.totalOutputTokens ?? 0) + this.#liveOutputTokens;
		return {
			input,
			output,
			cacheRead: 0,
			cacheWrite: 0,
			cost: 0,
			premiumRequests: 0,
		};
	}
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
		const backend = readToolBackend(meta);
		if (backend) details.backend = backend;
		const agent = readAgentStats(meta);
		if (agent) details.agent = agent;
	}
	return Object.keys(details).length > 0 ? details : undefined;
}

function readToolBackend(meta: Record<string, unknown> | undefined): { backend: string; kind?: string } | undefined {
	const raw = meta?.["mustang.agent/toolBackend"];
	if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
	const source = raw as Record<string, unknown>;
	const backend = source.backend;
	if (typeof backend !== "string" || !backend.trim()) return undefined;
	const kind = source.kind;
	return {
		backend,
		kind: typeof kind === "string" && kind.trim() ? kind : undefined,
	};
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

function contextUsageSnapshotFromUpdate(
	update: SessionUpdateParams,
	fallbackWindow?: number | null,
): ContextUsageSnapshot {
	const input = numberFromUpdate(update.inputTokens ?? update.input_tokens);
	const output = numberFromUpdate(update.outputTokens ?? update.output_tokens);
	const cacheRead = numberFromUpdate(update.cacheReadTokens ?? update.cache_read_tokens);
	const cacheWrite = numberFromUpdate(update.cacheWriteTokens ?? update.cache_write_tokens);
	const used = numberFromUpdate(update.used) || input + output + cacheRead + cacheWrite;
	const size = numberFromUpdate(update.size);
	const contextWindow = size > 0 ? size : fallbackWindow ?? null;
	const percent = contextWindow && contextWindow > 0 ? Number(((used / contextWindow) * 100).toFixed(1)) : 0;
	return {
		totalTokens: used,
		contextWindow,
		percent,
	};
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
	const firstMessage = title ? "" : session.sessionId;
	return {
		path: session.path || session.sessionId,
		id: session.sessionId,
		cwd: session.cwd || process.cwd(),
		title,
		created,
		modified,
		messageCount: session.messageCount ?? 0,
		firstMessage,
		allMessagesText: `${title ?? ""} ${session.cwd ?? ""} ${session.sessionId}`.trim(),
	};
}

function currentSessionInfo(
	session: MustangSession | undefined,
	sessionManager: MustangSessionManagerAdapter,
): SessionInfo | undefined {
	if (!session) return undefined;
	const now = new Date();
	const summary = session.summary;
	const title = sessionManager.getSessionName() || summary?.title?.trim() || "Untitled session";
	const created = parseDate(summary?.createdAt) ?? now;
	const modified = parseDate(summary?.updatedAt) ?? now;
	const cwd = summary?.cwd || sessionManager.getCwd();
	return {
		path: session.sessionId,
		id: session.sessionId,
		cwd,
		title,
		created,
		modified,
		messageCount: summary?.messageCount ?? 0,
		firstMessage: "",
		allMessagesText: `${title} ${cwd} ${session.sessionId}`.trim(),
	};
}

function createdSessionSummary(sessionId: string, cwd: string): CliSessionInfo {
	const timestamp = new Date().toISOString();
	return {
		sessionId,
		path: sessionId,
		title: "",
		cwd,
		updatedAt: timestamp,
		createdAt: timestamp,
		archivedAt: null,
		titleSource: null,
		totalInputTokens: null,
		totalOutputTokens: null,
		messageCount: 0,
		turnCount: 0,
		raw: { sessionId, cwd, createdAt: timestamp, updatedAt: timestamp },
	};
}

function parseDate(value: string | null | undefined): Date | undefined {
	if (!value) return undefined;
	const date = new Date(value);
	return Number.isNaN(date.getTime()) ? undefined : date;
}
