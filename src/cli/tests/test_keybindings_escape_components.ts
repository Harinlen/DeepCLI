import { setKeybindings } from "../src/active-port/tui/keybindings.js";
import { KeybindingsManager } from "../src/active-port/coding-agent/config/keybindings.js";
import { HookSelectorComponent } from "../src/active-port/coding-agent/modes/components/hook-selector.js";
import { SessionSelectorComponent } from "../src/active-port/coding-agent/modes/components/session-selector.js";
import { initTheme } from "../src/active-port/coding-agent/modes/theme/theme.js";
import { assert } from "./helpers.js";

await initTheme(false, "unicode", false, "dark", "dark");

function resetKeybindings(): void {
  setKeybindings(KeybindingsManager.inMemory());
}

resetKeybindings();

let hookCancelled = 0;
let hookSelected = "";
const hookSelector = new HookSelectorComponent(
  "Allow command?",
  ["Allow once", "Deny"],
  option => {
    hookSelected = option;
  },
  () => {
    hookCancelled += 1;
  },
);

hookSelector.handleInput("\x1b");
assert(hookCancelled === 1, "default Esc must cancel hook selector");
assert(hookSelected === "", "Esc must not select a hook option");

resetKeybindings();

let sessionCancelled = 0;
let sessionExited = 0;
const sessionSelector = new SessionSelectorComponent(
  [
    {
      path: "/tmp/session-a.jsonl",
      id: "session-a",
      cwd: "/tmp",
      title: "Alpha",
      created: new Date("2026-01-01T00:00:00Z"),
      modified: new Date("2026-01-01T00:00:00Z"),
      messageCount: 1,
      firstMessage: "alpha",
      allMessagesText: "alpha",
    },
  ],
  () => {},
  () => {
    sessionCancelled += 1;
  },
  () => {
    sessionExited += 1;
  },
);

sessionSelector.handleInput("\x1b");
assert(sessionCancelled === 1, "default Esc must cancel session selector");
sessionSelector.handleInput("\x03");
assert(sessionExited === 1, "Ctrl+C must keep its session selector exit behavior");

setKeybindings(KeybindingsManager.inMemory({ "tui.select.cancel": "ctrl+g" }));

let remappedCancelled = 0;
const remappedSelector = new HookSelectorComponent(
  "Remapped cancel",
  ["One", "Two"],
  () => {},
  () => {
    remappedCancelled += 1;
  },
);

remappedSelector.handleInput("\x1b");
assert(remappedCancelled === 0, "custom tui.select.cancel should replace default Esc for selector components");
remappedSelector.handleInput("\x07");
assert(remappedCancelled === 1, "custom tui.select.cancel key must cancel selector components");

resetKeybindings();

console.log("PASS: escape/cancel keybindings work for interactive selector components");
