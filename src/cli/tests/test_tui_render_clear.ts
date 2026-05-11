import { assert } from "./helpers.js";

const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { TUI } = await load("../src/active-port/tui/tui.ts");

class FakeTerminal {
	writes: string[] = [];
	columns = 10;
	rows = 5;
	kittyProtocolActive = false;
	appearance = undefined;
	start() {}
	stop() {}
	async drainInput() {}
	write(data: string) {
		this.writes.push(data);
	}
	moveBy() {}
	hideCursor() {}
	showCursor() {}
	clearLine() {}
	clearFromCursor() {}
	clearScreen() {}
	setTitle() {}
	onAppearanceChange() {}
}

class SingleLineComponent {
	constructor(public text: string) {}
	render() {
		return [this.text];
	}
	invalidate() {}
}

const terminal = new FakeTerminal();
const tui = new TUI(terminal as never);
const component = new SingleLineComponent("0123456789StatusBarTail");
tui.addChild(component as never);
tui.requestRender(true);
await Bun.sleep(0);

const firstFrame = terminal.writes.join("");
assert(firstFrame.includes("\x1b[2K"), "full render should clear each terminal row before writing content");
assert(firstFrame.includes("0123456789"), "full render should keep visible content inside terminal width");
assert(!firstFrame.includes("StatusBarTail"), "full render should truncate long lines before terminal auto-wrap");

console.log("PASS: tui render clear");
