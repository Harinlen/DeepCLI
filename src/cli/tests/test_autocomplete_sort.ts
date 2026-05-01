import { CombinedAutocompleteProvider, type SlashCommand } from "../src/active-port/tui/autocomplete.js";
import { KeybindingsManager } from "../src/active-port/coding-agent/config/keybindings.js";
import { createPromptActionAutocompleteProvider } from "../src/active-port/coding-agent/modes/prompt-action-autocomplete.js";
import { BUILTIN_COMMANDS, commandsToSlashCommands, sortCommandsByLabel } from "../src/modes/interactive.js";
import { assert } from "./helpers.js";

const commands: SlashCommand[] = [
	{ name: "help", description: "Show available commands" },
	{ name: "model", description: "Show or switch model" },
	{ name: "plan", description: "Enter, exit, or inspect plan mode" },
	{ name: "compact", description: "Compact conversation context" },
	{ name: "session", description: "List, resume, or delete sessions" },
	{ name: "cost", description: "Show usage and cost" },
	{ name: "memory", description: "List, show, or delete memories" },
	{ name: "cron", description: "Manage scheduled tasks" },
	{ name: "auth", description: "Manage secrets and auth values" },
	{ name: "quit", description: "Exit DeepCLI" },
	{ name: "exit", description: "Exit DeepCLI" },
];

const provider = new CombinedAutocompleteProvider(commands);

const all = await provider.getSuggestions(["/"], 0, 1);
assert(all !== null, "expected slash command suggestions");
assert(
	all.items.map(item => item.value).join(",") ===
		"auth,compact,cost,cron,exit,help,memory,model,plan,quit,session",
	"slash commands should be alphabetized when scores tie",
);

const m = await provider.getSuggestions(["/m"], 0, 2);
assert(m !== null, "expected filtered slash command suggestions");
assert(
	m.items.slice(0, 2).map(item => item.value).join(",") === "memory,model",
	"filtered slash commands should keep alphabetical tie-breaks",
);

const c = await provider.getSuggestions(["/c"], 0, 2);
assert(c !== null, "expected /c slash command suggestions");
assert(
	c.items.map(item => item.value).join(",") === "compact,cost,cron",
	"slash command filtering should match command names only",
);

const mustangCommands = commandsToSlashCommands(sortCommandsByLabel(BUILTIN_COMMANDS), {
	modelProfiles: [
		{ name: "sonnet", providerType: "anthropic", modelId: "claude-sonnet", isDefault: true },
		{ name: "gpt", providerType: "openai", modelId: "gpt-5.2", isDefault: false },
	],
	sessionList: [
		{
			sessionId: "sess-1",
			path: "sess-1",
			title: "Planning",
			cwd: "/tmp",
			updatedAt: null,
			createdAt: null,
			archivedAt: null,
			titleSource: null,
			totalInputTokens: null,
			totalOutputTokens: null,
			raw: { sessionId: "sess-1" },
		},
	],
	themeNames: ["dark", "light"],
});
const mustangProvider = new CombinedAutocompleteProvider(mustangCommands);

const sessionArgs = await mustangProvider.getSuggestions(["/session "], 0, 9);
assert(sessionArgs !== null, "expected /session argument suggestions");
assert(
	sessionArgs.items.map(item => item.value).includes("info") && sessionArgs.items.map(item => item.value).includes("delete"),
	"/session autocomplete should include omp-style info/delete actions",
);

const sessionSwitchArgs = await mustangProvider.getSuggestions(["/session switch "], 0, 16);
assert(sessionSwitchArgs !== null, "expected /session switch target suggestions");
assert(sessionSwitchArgs.items[0]?.value === "sess-1", "/session switch should complete recent session ids");

const modelArgs = await mustangProvider.getSuggestions(["/model switch "], 0, 14);
assert(modelArgs !== null, "expected /model switch profile suggestions");
assert(
	modelArgs.items.map(item => item.value).join(",") === "sonnet,gpt",
	"/model switch should complete live model profiles in kernel order",
);

const themeArgs = await mustangProvider.getSuggestions(["/theme set l"], 0, 12);
assert(themeArgs !== null, "expected /theme set suggestions");
assert(themeArgs.items[0]?.value === "light", "/theme set should complete loaded themes");

let copiedPrompt = false;
const promptActionProvider = createPromptActionAutocompleteProvider({
	commands,
	basePath: process.cwd(),
	keybindings: KeybindingsManager.inMemory(),
	copyCurrentLine: () => {},
	copyPrompt: () => {
		copiedPrompt = true;
	},
	undo: () => {},
	moveCursorToMessageEnd: () => {},
	moveCursorToMessageStart: () => {},
	moveCursorToLineStart: () => {},
	moveCursorToLineEnd: () => {},
});

const promptActions = await promptActionProvider.getSuggestions(["#"], 0, 1);
assert(promptActions !== null, "expected prompt action suggestions");
assert(
	promptActions.items.map(item => item.value).includes("Copy whole prompt"),
	"prompt actions should include oh-my-pi copy prompt action",
);

const copyPromptAction = promptActions.items.find(item => item.value === "Copy whole prompt");
assert(copyPromptAction !== undefined, "expected copy prompt action");
const applied = promptActionProvider.applyCompletion(["hello #"], 0, 7, copyPromptAction!, "#");
assert(applied.lines.join("\n") === "hello ", "prompt action completion should remove # trigger text");
applied.onApplied?.();
assert(copiedPrompt, "prompt action should execute selected action");

console.log("PASS: autocomplete sort");
