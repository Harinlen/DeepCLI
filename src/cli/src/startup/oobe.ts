import type { CliConfig, OobeConfig } from "@/config/schema.js";
import { saveCliOobeState } from "@/config/loader.js";
import type { ModelAddInput, ModelUpdateInput, ProviderModelState } from "@/models/service.js";

export const OOBE_REVISION = 1;
export const DEEPSEEK_API_KEYS_URL = "https://platform.deepseek.com/api_keys";
export const DEEPSEEK_PROVIDER_NAME = "deepseek";
export const DEEPSEEK_PROVIDER_TYPE = "deepseek";
export const DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com";

export const DEEPSEEK_PRESET_MODELS = [
  {
    modelId: "deepseek-v4-pro",
    displayName: "DeepSeek V4 Pro",
    contextWindow: 1_000_000,
    roles: ["default", "memory"],
  },
  {
    modelId: "deepseek-v4-flash",
    displayName: "DeepSeek V4 Flash",
    contextWindow: 1_000_000,
    roles: ["compact"],
  },
] as const;

export interface OobeModelClient {
  listProviderModels(): Promise<ProviderModelState>;
  addProviderModel(input: ModelAddInput): Promise<unknown>;
  updateProviderModel(input: ModelUpdateInput): Promise<unknown>;
  setCurrentModelRole(role: string, provider: string, model: string): Promise<boolean>;
}

export type OobeCheckResult =
  | { shouldShow: false; reason: "current-revision-state" | "satisfied"; stateToSave?: OobeConfig }
  | { shouldShow: true; reason: "missing-default-model" };

export async function checkOobe(client: Pick<OobeModelClient, "listProviderModels">, config: CliConfig): Promise<OobeCheckResult> {
  if (config.oobe?.revision === OOBE_REVISION && (config.oobe.status === "satisfied" || config.oobe.status === "skipped")) {
    return { shouldShow: false, reason: "current-revision-state" };
  }
  const state = await client.listProviderModels();
  if (hasCurrentDefaultModel(state)) {
    return {
      shouldShow: false,
      reason: "satisfied",
      stateToSave: {
        revision: OOBE_REVISION,
        status: "satisfied",
        checked_at: new Date().toISOString(),
        skipped_at: null,
      },
    };
  }
  return { shouldShow: true, reason: "missing-default-model" };
}

export function markOobeSatisfied(path: string, config: CliConfig): void {
  const state: OobeConfig = {
    revision: OOBE_REVISION,
    status: "satisfied",
    checked_at: new Date().toISOString(),
    skipped_at: null,
  };
  config.oobe = state;
  saveCliOobeState(path, state);
}

export function markOobeSkipped(path: string, config: CliConfig): void {
  const state: OobeConfig = {
    revision: OOBE_REVISION,
    status: "skipped",
    checked_at: null,
    skipped_at: new Date().toISOString(),
  };
  config.oobe = state;
  saveCliOobeState(path, state);
}

export async function configureDeepSeekPreset(client: OobeModelClient, apiKey: string): Promise<void> {
  const key = apiKey.trim();
  if (!key) throw new Error("API key must not be empty");
  const state = await client.listProviderModels();
  const deepseekProvider = state.providers.find(provider => provider.name === DEEPSEEK_PROVIDER_NAME);
  if (deepseekProvider && deepseekProvider.providerType !== DEEPSEEK_PROVIDER_TYPE) {
    throw new Error(`Provider '${DEEPSEEK_PROVIDER_NAME}' already exists with type '${deepseekProvider.providerType}'`);
  }
  for (const preset of DEEPSEEK_PRESET_MODELS) {
    const exists = Boolean(deepseekProvider?.models.includes(preset.modelId));
    if (exists) {
      await client.updateProviderModel({
        providerName: DEEPSEEK_PROVIDER_NAME,
        providerType: DEEPSEEK_PROVIDER_TYPE,
        apiKey: key,
        modelId: preset.modelId,
        displayName: preset.displayName,
        contextWindow: preset.contextWindow,
        roles: [...preset.roles],
      });
    } else {
      await client.addProviderModel({
        providerName: DEEPSEEK_PROVIDER_NAME,
        providerType: DEEPSEEK_PROVIDER_TYPE,
        apiKey: key,
        modelId: preset.modelId,
        displayName: preset.displayName,
        contextWindow: preset.contextWindow,
        roles: [...preset.roles],
      });
    }
  }
  await client.setCurrentModelRole("default", DEEPSEEK_PROVIDER_NAME, "deepseek-v4-pro");
  await client.setCurrentModelRole("memory", DEEPSEEK_PROVIDER_NAME, "deepseek-v4-pro");
  await client.setCurrentModelRole("compact", DEEPSEEK_PROVIDER_NAME, "deepseek-v4-flash");
}

export function hasCurrentDefaultModel(state: ProviderModelState): boolean {
  const ref = state.currentUsed.default;
  if (!ref) return false;
  return state.providers.some(provider => provider.name === ref[0] && provider.models.includes(ref[1]));
}
