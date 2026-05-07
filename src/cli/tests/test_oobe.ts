import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { assert } from "./helpers.js";
import { DEFAULT_CONFIG, type CliConfig } from "../src/config/schema.js";
import { loadCliConfig } from "../src/config/loader.js";
import {
  checkOobe,
  configureDeepSeekPreset,
  DEEPSEEK_PRESET_MODELS,
  markOobeSkipped,
  OOBE_REVISION,
  type OobeModelClient,
} from "../src/startup/oobe.js";
import type { ProviderModelState } from "../src/models/service.js";

function config(overrides: Partial<CliConfig> = {}): CliConfig {
  return {
    ...DEFAULT_CONFIG,
    kernel: { ...DEFAULT_CONFIG.kernel },
    session: { ...DEFAULT_CONFIG.session },
    ui: { ...DEFAULT_CONFIG.ui },
    oobe: null,
    ...overrides,
  };
}

const emptyState: ProviderModelState = {
  providers: [],
  providerTypeOptions: [{ providerType: "deepseek", settingFields: ["api_key", "base_url"], effectiveBaseUrl: "https://api.deepseek.com" }],
  models: [],
  currentUsed: {},
  defaultContextWindow: 128_000,
};

const satisfied = await checkOobe({
  async listProviderModels() {
    return {
      ...emptyState,
      providers: [{ name: "deepseek", providerType: "deepseek", hasApiKey: true, hasAwsSecretKey: false, settingFields: ["api_key", "base_url"], models: ["deepseek-v4-pro"], contextWindows: {}, displayNames: {}, roles: {} }],
      currentUsed: { default: ["deepseek", "deepseek-v4-pro"] },
    };
  },
}, config());
assert(satisfied.shouldShow === false && satisfied.reason === "satisfied", "OOBE should be satisfied when current/default exists");
assert(!satisfied.shouldShow && satisfied.reason === "satisfied" && satisfied.stateToSave?.revision === OOBE_REVISION, "satisfied OOBE should save current revision");

const skipped = await checkOobe({
  async listProviderModels() {
    throw new Error("should not be called");
  },
}, config({ oobe: { revision: OOBE_REVISION, status: "skipped", checked_at: null, skipped_at: "now" } }));
assert(skipped.shouldShow === false && skipped.reason === "current-revision-state", "current skipped revision should suppress OOBE checks");

const pending = await checkOobe({
  async listProviderModels() {
    return emptyState;
  },
}, config());
assert(pending.shouldShow === true, "OOBE should show when current/default is missing");

const calls: Array<{ method: string; input: unknown }> = [];
let providerModels: string[] = [];
const client: OobeModelClient = {
  async listProviderModels() {
    return {
      ...emptyState,
      providers: providerModels.length
        ? [{ name: "deepseek", providerType: "deepseek", hasApiKey: true, hasAwsSecretKey: false, settingFields: ["api_key", "base_url"], models: providerModels, contextWindows: {}, displayNames: {}, roles: {} }]
        : [],
    };
  },
  async addProviderModel(input) {
    calls.push({ method: "add", input });
    providerModels.push(input.modelId);
    return {};
  },
  async updateProviderModel(input) {
    calls.push({ method: "update", input });
    return {};
  },
  async setCurrentModelRole(role, provider, model) {
    calls.push({ method: "set", input: { role, provider, model } });
    return true;
  },
};
await configureDeepSeekPreset(client, "sk-test");
assert(calls.filter(call => call.method === "add").length === DEEPSEEK_PRESET_MODELS.length, "DeepSeek preset should add both models on a new provider");
assert(calls.some(call => call.method === "set" && (call.input as { role: string }).role === "default"), "DeepSeek preset should set default role");
assert(calls.some(call => call.method === "set" && (call.input as { role: string }).role === "compact"), "DeepSeek preset should set compact role");

calls.length = 0;
await configureDeepSeekPreset(client, "sk-test-2");
assert(calls.filter(call => call.method === "update").length === DEEPSEEK_PRESET_MODELS.length, "DeepSeek preset should update existing preset models idempotently");

const dir = mkdtempSync(join(tmpdir(), "deepcli-oobe-"));
try {
  const path = join(dir, "client.yaml");
  const cfg = config();
  markOobeSkipped(path, cfg);
  const raw = readFileSync(path, "utf8");
  assert(raw.includes("oobe:"), "skipping OOBE should persist an oobe section");
  const loaded = loadCliConfig({ path, env: {} });
  assert(loaded.config.oobe?.status === "skipped", "saved OOBE state should load");
} finally {
  rmSync(dir, { recursive: true, force: true });
}

console.log("PASS: OOBE state and DeepSeek preset");
