import { CombinedAutocompleteProvider, Editor, type EditorTheme, type SlashCommand } from "../src/active-port/tui/index.js";
import { assert } from "./helpers.js";

const tick = () => new Promise(resolve => setTimeout(resolve, 0));

const commands: SlashCommand[] = [
	{
		name: "model",
		description: "Manage models",
		getArgumentCompletions: argumentPrefix => {
			const items = [
				{ value: "list", label: "list", description: "List configured model profiles" },
				{ value: "add", label: "add", description: "Add a model" },
				{ value: "current", label: "current", description: "Show current-used roles" },
				{ value: "use", label: "use", description: "Set current-used role" },
			];
			const prefix = argumentPrefix.toLowerCase();
			const filtered = items.filter(item => item.value.startsWith(prefix));
			return filtered.length > 0 ? filtered : null;
		},
	},
];

const plain = (text: string) => text;
const editorTheme: EditorTheme = {
	borderColor: plain,
	hintStyle: plain,
	selectList: {
		selectedPrefix: plain,
		selectedText: plain,
		description: plain,
		scrollInfo: plain,
		noMatch: plain,
		symbols: {
			cursor: ">",
			inputCursor: "|",
			boxRound: { topLeft: "+", topRight: "+", bottomLeft: "+", bottomRight: "+", horizontal: "-", vertical: "|" },
			boxSharp: {
				topLeft: "+",
				topRight: "+",
				bottomLeft: "+",
				bottomRight: "+",
				horizontal: "-",
				vertical: "|",
				teeDown: "+",
				teeUp: "+",
				teeLeft: "+",
				teeRight: "+",
				cross: "+",
			},
			table: {
				topLeft: "+",
				topRight: "+",
				bottomLeft: "+",
				bottomRight: "+",
				horizontal: "-",
				vertical: "|",
				teeDown: "+",
				teeUp: "+",
				teeLeft: "+",
				teeRight: "+",
				cross: "+",
			},
			quoteBorder: "|",
			hrChar: "-",
			spinnerFrames: ["-"],
		},
	},
	symbols: {
		cursor: ">",
		inputCursor: "|",
		boxRound: { topLeft: "+", topRight: "+", bottomLeft: "+", bottomRight: "+", horizontal: "-", vertical: "|" },
		boxSharp: {
			topLeft: "+",
			topRight: "+",
			bottomLeft: "+",
			bottomRight: "+",
			horizontal: "-",
			vertical: "|",
			teeDown: "+",
			teeUp: "+",
			teeLeft: "+",
			teeRight: "+",
			cross: "+",
		},
		table: {
			topLeft: "+",
			topRight: "+",
			bottomLeft: "+",
			bottomRight: "+",
			horizontal: "-",
			vertical: "|",
			teeDown: "+",
			teeUp: "+",
			teeLeft: "+",
			teeRight: "+",
			cross: "+",
		},
		quoteBorder: "|",
		hrChar: "-",
		spinnerFrames: ["-"],
	},
};

function visibleEditorText(editor: Editor): string {
	return Bun.stripANSI(editor.render(80).join("\n"));
}

async function type(editor: Editor, text: string): Promise<void> {
	for (const char of text) {
		editor.handleInput(char);
		await tick();
	}
}

const editor = new Editor(editorTheme);
editor.setAutocompleteProvider(new CombinedAutocompleteProvider(commands));

await type(editor, "/model ");

let rendered = visibleEditorText(editor);
assert(rendered.includes("list"), "slash autocomplete should switch to /model argument suggestions after space");
assert(rendered.includes("add"), "slash autocomplete should show /model add after space");
assert(editor.isShowingAutocomplete(), "autocomplete should remain open for slash command arguments");

editor.handleInput("\x7f");
await tick();
assert(editor.getText() === "/model", "backspace should remove the argument-space");
assert(editor.isShowingAutocomplete(), "autocomplete should reopen for the slash command name after backspace");

editor.handleInput(" ");
await tick();
rendered = visibleEditorText(editor);
assert(rendered.includes("list"), "slash argument autocomplete should survive space after backspace");
assert(rendered.includes("add"), "slash argument autocomplete should survive repeated space entry");
assert(editor.isShowingAutocomplete(), "autocomplete should remain open after repeated slash argument transition");

console.log("PASS: editor slash argument autocomplete");
