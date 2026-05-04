import { MustangMethod } from "@/acp/methods.js";

export interface ModelProfile {
  name: string;
  providerName: string;
  providerType: string;
  modelId: string;
  contextWindow?: number | null;
  isDefault: boolean;
}

export interface ModelState {
  profiles: ModelProfile[];
  defaultModel: string;
}

export interface ModelProviderInfo {
  name: string;
  providerType: string;
  models: string[];
  contextWindows: Record<string, number>;
  roles: Record<string, boolean>;
}

export interface ProviderModelItem {
  displayName: string;
  providerName: string;
  providerType: string;
  modelId: string;
  roles: string[];
  contextWindow?: number | null;
}

export interface ProviderModelState {
  providers: ModelProviderInfo[];
  models: ProviderModelItem[];
  currentUsed: Record<string, [string, string]>;
  defaultContextWindow: number;
}

export interface ModelServiceClient {
  request<R = unknown>(method: string, params: unknown, opts?: { timeoutMs?: number }): Promise<R>;
}

interface RawProfile {
  name?: unknown;
  providerName?: unknown;
  provider_name?: unknown;
  providerType?: unknown;
  provider_type?: unknown;
  modelId?: unknown;
  model_id?: unknown;
  contextWindow?: unknown;
  context_window?: unknown;
  isDefault?: unknown;
  is_default?: unknown;
}

interface RawProfileListResponse {
  profiles?: RawProfile[];
  defaultModel?: unknown;
  default_model?: unknown;
}

interface RawProvider {
  name?: unknown;
  providerType?: unknown;
  provider_type?: unknown;
  models?: unknown;
  contextWindows?: unknown;
  context_windows?: unknown;
  roles?: unknown;
}

interface RawProviderListResponse {
  providers?: RawProvider[];
  currentUsed?: unknown;
  current_used?: unknown;
  defaultContextWindow?: unknown;
  default_context_window?: unknown;
}

export class ModelService {
  constructor(private readonly client: ModelServiceClient) {}

  async listProfiles(): Promise<ModelState> {
    const response = await this.client.request<RawProfileListResponse>(
      MustangMethod.modelProfileList,
      {},
      { timeoutMs: 10_000 },
    );
    const profiles = (response.profiles ?? []).map(mapProfile).filter((profile): profile is ModelProfile => profile !== null);
    const defaultModel = String(response.defaultModel ?? response.default_model ?? "");
    return { profiles, defaultModel };
  }

  async setDefault(profile: ModelProfile): Promise<string> {
    const result = await this.setCurrent("default", profile.providerName, profile.modelId);
    return `${result.provider}/${result.model}`;
  }

  async listProviders(): Promise<ProviderModelState> {
    const [response, profileState] = await Promise.all([
      this.client.request<RawProviderListResponse>(
        MustangMethod.modelProviderList,
        {},
        { timeoutMs: 10_000 },
      ),
      this.listProfiles().catch(() => ({ profiles: [], defaultModel: "" })),
    ]);
    const currentUsed = mapCurrentUsed(response.currentUsed ?? response.current_used);
    const defaultContextWindow = positiveNumberOrNull(response.defaultContextWindow ?? response.default_context_window);
    if (defaultContextWindow === null) {
      throw new Error("model/provider_list response is missing defaultContextWindow");
    }
    const providers = (response.providers ?? []).map(mapProvider).filter((provider): provider is ModelProviderInfo => provider !== null);
    const profileByRef = new Map<string, ModelProfile>();
    for (const profile of profileState.profiles) {
      profileByRef.set(`${profile.providerName}/${profile.modelId}`, profile);
    }
    const models: ProviderModelItem[] = [];
    for (const provider of providers) {
      for (const modelId of provider.models) {
        const profile = profileByRef.get(`${provider.name}/${modelId}`);
        models.push({
          displayName: modelDisplayName(profile, provider.name, modelId),
          providerName: provider.name,
          providerType: provider.providerType,
          modelId,
          roles: rolesForModel(currentUsed, provider.name, modelId),
          contextWindow: provider.contextWindows[modelId] ?? profile?.contextWindow ?? defaultContextWindow,
        });
      }
    }
    return { providers, models, currentUsed, defaultContextWindow };
  }

  async setCurrent(
    role: string,
    provider: string,
    model: string,
  ): Promise<{ role: string; provider: string; model: string }> {
    const response = await this.client.request<{ role?: unknown; model?: unknown }>(
      MustangMethod.modelSetCurrent,
      {
        role,
        provider,
        model,
      },
    );
    const ref = Array.isArray(response.model) ? response.model : [provider, model];
    return {
      role: String(response.role ?? role),
      provider: String(ref[0] ?? provider),
      model: String(ref[1] ?? model),
    };
  }
}

function mapProfile(raw: RawProfile): ModelProfile | null {
  const name = String(raw.name ?? "");
  const providerName = String(raw.providerName ?? raw.provider_name ?? name.split("/")[0] ?? "");
  const providerType = String(raw.providerType ?? raw.provider_type ?? "");
  const modelId = String(raw.modelId ?? raw.model_id ?? "");
  if (!name || !providerName || !providerType || !modelId) return null;
  return {
    name,
    providerName,
    providerType,
    modelId,
    contextWindow: numberOrNull(raw.contextWindow ?? raw.context_window),
    isDefault: Boolean(raw.isDefault ?? raw.is_default),
  };
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function positiveNumberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

function modelDisplayName(profile: ModelProfile | undefined, provider: string, model: string): string {
  if (!profile?.name || profile.name === `${provider}/${model}`) return model;
  return profile.name;
}

function mapProvider(raw: RawProvider): ModelProviderInfo | null {
  const name = String(raw.name ?? "");
  const providerType = String(raw.providerType ?? raw.provider_type ?? "");
  const models = Array.isArray(raw.models) ? raw.models.map(item => String(item)).filter(Boolean) : [];
  const contextWindows = mapContextWindows(raw.contextWindows ?? raw.context_windows);
  const roles = isRecord(raw.roles)
    ? Object.fromEntries(Object.entries(raw.roles).map(([key, value]) => [key, Boolean(value)]))
    : {};
  if (!name || !providerType) return null;
  return { name, providerType, models, contextWindows, roles };
}

function mapContextWindows(value: unknown): Record<string, number> {
  if (!isRecord(value)) return {};
  const result: Record<string, number> = {};
  for (const [model, raw] of Object.entries(value)) {
    if (typeof raw === "number" && Number.isFinite(raw) && raw > 0) {
      result[model] = raw;
    }
  }
  return result;
}

function mapCurrentUsed(value: unknown): Record<string, [string, string]> {
  if (!isRecord(value)) return {};
  const result: Record<string, [string, string]> = {};
  for (const [role, ref] of Object.entries(value)) {
    if (!Array.isArray(ref) || ref.length !== 2) continue;
    const provider = String(ref[0] ?? "");
    const model = String(ref[1] ?? "");
    if (provider && model) result[role] = [provider, model];
  }
  return result;
}

function rolesForModel(currentUsed: Record<string, [string, string]>, provider: string, model: string): string[] {
  return Object.entries(currentUsed)
    .filter(([, ref]) => ref[0] === provider && ref[1] === model)
    .map(([role]) => role)
    .sort();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
