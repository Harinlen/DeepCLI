import { type AutocompleteItem, type SlashCommand } from "@/tui/index.js";
import type { AcpClient } from "@/acp/client.js";
import { MustangSession } from "@/session.js";
import type { ModelProfile } from "@/models/service.js";
import { SessionService } from "@/sessions/service.js";
import type { CliSessionInfo } from "@/sessions/types.js";
import { MustangAgentSessionAdapter } from "@/session/agent-session-adapter.js";
import { PermissionController } from "@/permissions/controller.js";

export const BUILTIN_COMMANDS: AutocompleteItem[] = [
  { value: "help", label: "/help", description: "Show available commands" },
  { value: "model", label: "/model", description: "Show or switch model" },
  { value: "plan", label: "/plan", description: "Enter, exit, or inspect plan mode" },
  { value: "compact", label: "/compact", description: "Compact conversation context" },
  { value: "session", label: "/session", description: "List, resume, or delete sessions" },
  { value: "theme", label: "/theme", description: "Show or switch theme" },
  { value: "cost", label: "/cost", description: "Show usage and cost" },
  { value: "memory", label: "/memory", description: "List, show, or delete memories" },
  { value: "auth", label: "/auth", description: "Manage secrets and auth values" },
  { value: "quit", label: "/quit", description: "Exit DeepCLI" },
  { value: "exit", label: "/exit", description: "Exit DeepCLI" },
];

export function sortCommandsByLabel(commands: AutocompleteItem[]): AutocompleteItem[] {
  return [...commands].sort((a, b) => {
    const aKey = a.label.replace(/^\//, "").toLowerCase();
    const bKey = b.label.replace(/^\//, "").toLowerCase();
    if (aKey < bKey) return -1;
    if (aKey > bKey) return 1;
    return 0;
  });
}

export function commandsToSlashCommands(
  commands: AutocompleteItem[],
  options: {
    modelProfiles?: ModelProfile[];
    sessionList?: CliSessionInfo[];
    themeNames?: string[];
  } = {},
): SlashCommand[] {
  return commands.map((command) => ({
    name: command.value,
    description: command.description,
    getArgumentCompletions: getArgumentCompletionFactory(command.value, options),
  }));
}

function getArgumentCompletionFactory(
  commandName: string,
  options: { modelProfiles?: ModelProfile[]; sessionList?: CliSessionInfo[]; themeNames?: string[] },
): ((argumentPrefix: string) => AutocompleteItem[] | null) | undefined {
  switch (commandName) {
    case "session":
      return (argumentPrefix) => completeSessionArguments(argumentPrefix, options.sessionList ?? []);
    case "model":
      return (argumentPrefix) => completeModelArguments(argumentPrefix, options.modelProfiles ?? []);
    case "plan":
      return (argumentPrefix) => filterCompletions(argumentPrefix, [
        { value: "enter", label: "enter", description: "Enter plan mode" },
        { value: "exit", label: "exit", description: "Exit plan mode" },
        { value: "status", label: "status", description: "Show plan mode status" },
      ]);
    case "theme":
      return (argumentPrefix) => completeThemeArguments(argumentPrefix, options.themeNames ?? []);
    default:
      return undefined;
  }
}

function completeSessionArguments(argumentPrefix: string, sessions: CliSessionInfo[]): AutocompleteItem[] | null {
  const [subcommand = "", value = ""] = argumentPrefix.split(/\s+/, 2);
  if (argumentPrefix.includes(" ") && (subcommand === "switch" || subcommand === "load")) {
    return filterCompletions(value, sessions.map((session, index) => ({
      value: session.sessionId,
      label: `${index + 1} ${session.title}`,
      description: session.cwd,
    })));
  }
  if (argumentPrefix.includes(" ") && subcommand === "delete") {
    return filterCompletions(value, [{ value: "confirm", label: "confirm", description: "Permanently delete current session" }]);
  }
  return filterCompletions(subcommand, [
    { value: "info", label: "info", description: "Show session info and stats" },
    { value: "current", label: "current", description: "Show current session" },
    { value: "list", label: "list", description: "List recent sessions" },
    { value: "new", label: "new", description: "Create and switch to a new session" },
    { value: "load", label: "load", description: "Load a session by id" },
    { value: "switch", label: "switch", description: "Switch by list number or id" },
    { value: "rename", label: "rename", description: "Rename current session" },
    { value: "archive", label: "archive", description: "Archive current session" },
    { value: "unarchive", label: "unarchive", description: "Unarchive current session" },
    { value: "delete", label: "delete", description: "Delete current session and return to selector" },
  ]);
}

function completeModelArguments(argumentPrefix: string, profiles: ModelProfile[]): AutocompleteItem[] | null {
  const [subcommand = "", value = ""] = argumentPrefix.split(/\s+/, 2);
  if (argumentPrefix.includes(" ") && (subcommand === "switch" || subcommand === "set")) {
    return filterCompletions(value, profiles.map((profile) => ({
      value: profile.name,
      label: profile.name,
      description: `${profile.providerType}/${profile.modelId}${profile.isDefault ? " (default)" : ""}`,
    })));
  }
  return filterCompletions(subcommand, [
    { value: "list", label: "list", description: "List configured model profiles" },
    { value: "switch", label: "switch", description: "Switch default model profile" },
    { value: "set", label: "set", description: "Switch default model profile" },
  ]);
}

function completeThemeArguments(argumentPrefix: string, themeNames: string[]): AutocompleteItem[] | null {
  const [subcommand = "", value = ""] = argumentPrefix.split(/\s+/, 2);
  if (argumentPrefix.includes(" ") && subcommand === "set") {
    return filterCompletions(value, themeNames.map((name) => ({ value: name, label: name })));
  }
  return filterCompletions(subcommand, [
    { value: "current", label: "current", description: "Show current theme" },
    { value: "list", label: "list", description: "List available themes" },
    { value: "set", label: "set", description: "Set theme" },
  ]);
}

function filterCompletions(prefix: string, items: AutocompleteItem[]): AutocompleteItem[] | null {
  const normalized = prefix.toLowerCase();
  const filtered = items.filter((item) => item.value.toLowerCase().startsWith(normalized));
  return filtered.length > 0 ? filtered : null;
}

export class InteractiveMode {
  private readonly adapter: MustangAgentSessionAdapter;
  private mode: any;
  private resolveDone?: () => void;
  private connectionIndicatorTimer?: ReturnType<typeof setInterval>;

  constructor(
    private readonly client: AcpClient,
    session: MustangSession,
    private readonly options: {
      model?: string;
      provider?: string;
      sessionService?: SessionService;
      recentSessions?: CliSessionInfo[];
      theme?: { theme: string; auto_theme: boolean; symbols: string; status_line: boolean; welcome_recent: number };
      version?: string;
    } = {},
  ) {
    const sessionService = options.sessionService ?? new SessionService(client);
    this.adapter = new MustangAgentSessionAdapter({
      client,
      session,
      sessionService,
      recentSessions: options.recentSessions,
      defaultModel: options.model,
    });
  }

  async run(): Promise<void> {
    await this.adapter.refreshModelProfiles().catch(() => {});
    const { InteractiveMode: OmpInteractiveMode } = await importActivePortInteractiveMode();
    this.mode = new OmpInteractiveMode(this.adapter as never, this.options.version ?? "0.1.0");
    this.applyConnectionState(this.client.getConnectionState());
    this.client.onConnectionStateChange((state) => this.applyConnectionState(state));
    this.client.setPermissionHandler((_id, req) => {
      const controller = new PermissionController(this.mode, () => {});
      return controller.handleRequest(req);
    });
    this.client.onDisconnect((error) => {
      this.mode.showError(error.message);
    });
    this.client.onReconnect(() => {
      this.mode.showError("Kernel connection restored.");
    });
    await this.mode.init();
    void this.inputLoop();
    await new Promise<void>((resolve) => {
      this.resolveDone = resolve;
    });
  }

  private applyConnectionState(state: "connected" | "connecting" | "disconnected"): void {
    this.adapter.setKernelConnectionState(state);
    this.mode?.updateEditorTopBorder?.();
    this.mode?.ui?.requestRender?.();
    if (state === "connecting") {
      this.startConnectionIndicatorTimer();
    } else {
      this.stopConnectionIndicatorTimer();
    }
  }

  private startConnectionIndicatorTimer(): void {
    if (this.connectionIndicatorTimer) return;
    this.connectionIndicatorTimer = setInterval(() => {
      this.mode?.updateEditorTopBorder?.();
      this.mode?.ui?.requestRender?.();
    }, 180);
    this.connectionIndicatorTimer.unref?.();
  }

  private stopConnectionIndicatorTimer(): void {
    if (!this.connectionIndicatorTimer) return;
    clearInterval(this.connectionIndicatorTimer);
    this.connectionIndicatorTimer = undefined;
  }

  private async inputLoop(): Promise<void> {
    while (true) {
      const input = await this.mode.getUserInput();
      if (input.cancelled) continue;
      try {
        if (!input.started && !this.mode.markPendingSubmissionStarted(input)) continue;
        await this.adapter.prompt(input.text, { images: input.images });
      } catch (error) {
        this.mode.showError(error instanceof Error ? error.message : String(error));
      } finally {
        this.mode.finishPendingSubmission(input);
      }
    }
  }
}

async function importActivePortInteractiveMode(): Promise<{ InteractiveMode: new (...args: any[]) => any }> {
  const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<unknown>;
  return await load("../active-port/coding-agent/modes/interactive-mode.ts") as { InteractiveMode: new (...args: any[]) => any };
}
