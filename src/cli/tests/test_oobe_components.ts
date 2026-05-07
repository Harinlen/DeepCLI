import { assert } from "./helpers.js";

const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<any>;
const { initTheme } = await load("../src/active-port/coding-agent/modes/theme/theme.ts");
await initTheme(false);
const { OobeWelcomeComponent } = await load("../src/active-port/coding-agent/modes/components/oobe-welcome.ts");
const { DeepSeekOobeSetupComponent } = await load("../src/active-port/coding-agent/modes/components/deepseek-oobe-setup.ts");

const choices: string[] = [];
const welcome = new OobeWelcomeComponent((choice: string) => choices.push(choice));
welcome.focused = true;
let welcomeText = Bun.stripANSI(welcome.render(100).join("\n"));
assert(welcomeText.includes("Welcome to DeepCLI"), "OOBE welcome should render a title");
assert(welcomeText.includes("Set up DeepSeek"), "OOBE welcome should offer DeepSeek setup");
assert(welcomeText.includes("Set up others"), "OOBE welcome should offer the generic setup path");
welcome.handleInput("\x1b[B");
welcome.handleInput("\n");
assert(choices[0] === "others", "OOBE welcome should select Set up others");

const addModelWelcome = new OobeWelcomeComponent(
  () => {},
  {
    title: "Set up a model",
    lines: ["No providers are configured yet.", "Set up DeepSeek quickly, or add another model provider."],
    skipLabel: "Cancel",
  },
);
const addModelText = Bun.stripANSI(addModelWelcome.render(100).join("\n"));
assert(addModelText.includes("Set up a model"), "/model add entry should render command-context title");
assert(addModelText.includes("No providers are configured yet."), "/model add entry should explain the missing provider state");
assert(addModelText.includes("Cancel"), "/model add entry should use command-context cancel label");
assert(!addModelText.includes("Welcome to DeepCLI"), "/model add entry should not render startup welcome copy");
assert(!addModelText.includes("Skip to main window"), "/model add entry should not render startup skip copy");

const ctrlCChoices: string[] = [];
const startupWelcome = new OobeWelcomeComponent((choice: string) => ctrlCChoices.push(choice), { ctrlCChoice: "exit" });
startupWelcome.handleInput("\x03");
assert(ctrlCChoices[0] === "exit", "startup OOBE should treat Ctrl+C as exit");
const commandWelcome = new OobeWelcomeComponent((choice: string) => ctrlCChoices.push(choice));
commandWelcome.handleInput("\x03");
assert(ctrlCChoices[1] === "skip", "command-context OOBE reuse should treat Ctrl+C as cancel");

const saved: string[] = [];
const tui = { requestRender: () => {} } as any;
const setup = new DeepSeekOobeSetupComponent(
  tui,
  "",
  "https://api.deepseek.com",
  (apiKey: string) => {
    saved.push(apiKey);
  },
  () => {},
);
let setupText = Bun.stripANSI(setup.render(100).join("\n"));
assert(setupText.includes("https://platform.deepseek.com/api_keys"), "DeepSeek OOBE should show the API key URL");
assert(setupText.includes("DeepSeek V4 Pro <1M>"), "DeepSeek OOBE should summarize V4 Pro preset");
assert(setupText.includes("DeepSeek V4 Flash <1M>"), "DeepSeek OOBE should summarize V4 Flash preset");

setup.focused = true;
setup.handleInput("\x1b[200~sk-deepseek-test\x1b[201~");
setup.handleInput("\n");
assert(saved[0] === "sk-deepseek-test", "DeepSeek OOBE should accept bracketed paste and save the API key");

const exited: string[] = [];
const setupExit = new DeepSeekOobeSetupComponent(tui, "", "https://api.deepseek.com", () => {}, () => {}, () => exited.push("exit"));
setupExit.handleInput("\x03");
assert(exited[0] === "exit", "DeepSeek OOBE should treat Ctrl+C as exit when provided");

console.log("PASS: OOBE components");
