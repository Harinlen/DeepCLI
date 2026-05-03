import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadCliConfig, parseClientConfig } from "../src/config/loader.js";
import { defaultClientConfigPath, defaultTokenFilePath, expandHome } from "../src/config/paths.js";
import { assert } from "./helpers.js";

const missing = join(tmpdir(), `deepcli-missing-${Date.now()}.yaml`);
const defaults = loadCliConfig({ path: missing, env: {} });
assert(defaults.config.kernel.url === "ws://localhost:8200", "missing config should use default kernel URL");
assert(defaults.config.session.startup === "new", "missing config should create a new session by default");

const dir = mkdtempSync(join(tmpdir(), "deepcli-cli-config-"));
try {
  const path = join(dir, "client.yaml");
  writeFileSync(path, [
    "kernel:",
    "  url: ws://localhost:9000",
    "  token: config-token",
    "session:",
    "  startup: last",
    "  picker_limit: 7",
    "ui:",
    "  theme: light",
    "  symbols: ascii",
  ].join("\n"));

  const loaded = loadCliConfig({
    path,
    env: { KERNEL_PORT: "9100", MUSTANG_TOKEN: "env-token" },
    args: { port: 9200, theme: "dark-midnight" },
  });
  assert(loaded.config.kernel.url === "ws://localhost:9200", "argv port should override env and config URL");
  assert(loaded.config.kernel.health_url === "http://localhost:9200/", "argv port should update health URL");
  assert(loaded.config.kernel.token === "env-token", "env token should override literal config token");
  assert(loaded.config.session.startup === "last", "config session startup should load");
  assert(loaded.config.session.picker_limit === 7, "numeric config field should load");
  assert(loaded.config.ui.theme === "dark-midnight", "argv theme should override config theme");
  assert(loaded.config.ui.symbols === "ascii", "symbol preset should load");

	  const parsed = parseClientConfig("{\"ui\":{\"theme\":\"dark\"}}");
	  assert((parsed.ui as { theme: string }).theme === "dark", "JSON config should parse");

  const nativeRoot = join(dir, "native");
  const nativeConfigDir = join(nativeRoot, "config");
  const nativeStateDir = join(nativeRoot, "state");
  mkdirSync(nativeConfigDir, { recursive: true });
  mkdirSync(nativeStateDir, { recursive: true });
  writeFileSync(join(nativeConfigDir, "client.yaml"), [
    "kernel:",
    "  url: ws://localhost:9300",
    "ui:",
    "  theme: native",
  ].join("\n"));

  const nativeLoaded = loadCliConfig({
    env: {
      DEEPCLI_CONFIG_DIR: nativeConfigDir,
      DEEPCLI_STATE_DIR: nativeStateDir,
      DEEPCLI_TOKEN: "deepcli-token",
      MUSTANG_TOKEN: "legacy-token",
    },
  });
  assert(nativeLoaded.path === join(nativeConfigDir, "client.yaml"), "native config dir should be default config path");
  assert(nativeLoaded.config.kernel.url === "ws://localhost:9300", "native config file should load");
  assert(nativeLoaded.config.kernel.token === "deepcli-token", "DEEPCLI_TOKEN should override legacy token env");
  assert(nativeLoaded.config.kernel.token_file === join(nativeStateDir, "auth_token"), "native state dir should be default token file");

  assert(
    defaultClientConfigPath({ APPDATA: "C:\\Users\\saki\\AppData\\Roaming" }, "win32")
      === "C:\\Users\\saki\\AppData\\Roaming\\DeepCLI\\client.yaml",
    "Windows config path should use APPDATA",
  );
  assert(
    defaultTokenFilePath({ LOCALAPPDATA: "C:\\Users\\saki\\AppData\\Local" }, "win32")
      === "C:\\Users\\saki\\AppData\\Local\\DeepCLI\\State\\auth_token",
    "Windows token path should use LOCALAPPDATA",
  );
  assert(expandHome("~\\AppData").includes("AppData"), "Windows-style home expansion should work");
	} finally {
	  rmSync(dir, { recursive: true, force: true });
	}

console.log("PASS: config loader defaults, YAML/JSON, and precedence");
