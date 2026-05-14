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
  baseUrl?: string | null;
  effectiveBaseUrl?: string | null;
  awsRegion?: string | null;
  hasApiKey: boolean;
  apiKeyDisplay?: string | null;
  hasAwsSecretKey: boolean;
  awsSecretKeyDisplay?: string | null;
  settingFields: string[];
  models: string[];
  contextWindows: Record<string, number>;
  displayNames: Record<string, string>;
  roles: Record<string, boolean>;
}

export interface ProviderTypeInfo {
  providerType: string;
  settingFields: string[];
  effectiveBaseUrl?: string | null;
}

export interface ProviderModelItem {
  displayName: string;
  providerName: string;
  providerType: string;
  providerBaseUrl?: string | null;
  providerEffectiveBaseUrl?: string | null;
  providerAwsRegion?: string | null;
  providerHasApiKey: boolean;
  providerApiKeyDisplay?: string | null;
  providerHasAwsSecretKey: boolean;
  providerAwsSecretKeyDisplay?: string | null;
  providerSettingFields: string[];
  modelId: string;
  roles: string[];
  contextWindow?: number | null;
}

export interface ModelUpdateInput {
  providerName: string;
  newProviderName?: string | null;
  providerType?: string | null;
  apiKey?: string | null;
  baseUrl?: string | null;
  awsSecretKey?: string | null;
  awsRegion?: string | null;
  modelId: string;
  newModelId?: string | null;
  displayName?: string | null;
  contextWindow?: number | null;
  roles?: string[];
}

export interface ModelAddInput {
  providerName: string;
  providerType?: string | null;
  apiKey?: string | null;
  baseUrl?: string | null;
  awsSecretKey?: string | null;
  awsRegion?: string | null;
  modelId: string;
  displayName?: string | null;
  contextWindow?: number | null;
  roles?: string[];
}

export interface ProviderModelState {
  providers: ModelProviderInfo[];
  providerTypeOptions: ProviderTypeInfo[];
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
  baseUrl?: unknown;
  base_url?: unknown;
  effectiveBaseUrl?: unknown;
  effective_base_url?: unknown;
  awsRegion?: unknown;
  aws_region?: unknown;
  hasApiKey?: unknown;
  has_api_key?: unknown;
  apiKeyDisplay?: unknown;
  api_key_display?: unknown;
  hasAwsSecretKey?: unknown;
  has_aws_secret_key?: unknown;
  awsSecretKeyDisplay?: unknown;
  aws_secret_key_display?: unknown;
  settingFields?: unknown;
  setting_fields?: unknown;
  models?: unknown;
  contextWindows?: unknown;
  context_windows?: unknown;
  displayNames?: unknown;
  display_names?: unknown;
  roles?: unknown;
}

interface RawProviderListResponse {
  providers?: RawProvider[];
  providerTypeOptions?: unknown;
  provider_type_options?: unknown;
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
    const providerTypeOptions = mapProviderTypeOptions(response.providerTypeOptions ?? response.provider_type_options, providers);
    const profileByRef = new Map<string, ModelProfile>();
    for (const profile of profileState.profiles) {
      profileByRef.set(`${profile.providerName}/${profile.modelId}`, profile);
    }
    const models: ProviderModelItem[] = [];
    for (const provider of providers) {
      for (const modelId of provider.models) {
        const profile = profileByRef.get(`${provider.name}/${modelId}`);
        models.push({
          displayName: provider.displayNames[modelId] ?? modelDisplayName(profile, provider.name, modelId),
          providerName: provider.name,
          providerType: provider.providerType,
          providerBaseUrl: provider.baseUrl,
          providerEffectiveBaseUrl: provider.effectiveBaseUrl,
          providerAwsRegion: provider.awsRegion,
          providerHasApiKey: provider.hasApiKey,
          providerApiKeyDisplay: provider.apiKeyDisplay,
          providerHasAwsSecretKey: provider.hasAwsSecretKey,
          providerAwsSecretKeyDisplay: provider.awsSecretKeyDisplay,
          providerSettingFields: provider.settingFields,
          modelId,
          roles: rolesForModel(currentUsed, provider.name, modelId),
          contextWindow: provider.contextWindows[modelId] ?? profile?.contextWindow ?? defaultContextWindow,
        });
      }
    }
    return { providers, providerTypeOptions, models, currentUsed, defaultContextWindow };
  }

  async getThinking(): Promise<boolean> {
    const response = await this.client.request<{ enabled?: unknown }>(
      MustangMethod.llmThinkingGet,
      {},
      { timeoutMs: 10_000 },
    );
    return Boolean(response.enabled);
  }

  async setThinking(enabled: boolean): Promise<boolean> {
    const response = await this.client.request<{ enabled?: unknown }>(
      MustangMethod.llmThinkingSet,
      { enabled },
      { timeoutMs: 10_000 },
    );
    return Boolean(response.enabled);
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

  async updateModel(input: ModelUpdateInput): Promise<ProviderModelItem> {
    const response = await this.client.request<RawModelWriteResponse>(
      MustangMethod.modelUpdate,
      {
        provider: input.providerName,
        model: input.modelId,
        providerName: input.newProviderName ?? undefined,
        providerType: input.providerType ?? undefined,
        apiKey: input.apiKey ?? undefined,
        baseUrl: input.baseUrl ?? undefined,
        awsSecretKey: input.awsSecretKey ?? undefined,
        awsRegion: input.awsRegion ?? undefined,
        modelId: input.newModelId ?? undefined,
        displayName: input.displayName ?? null,
        contextWindow: input.contextWindow ?? null,
        roles: input.roles,
      },
      { timeoutMs: 10_000 },
    );
    return mapModelWriteResponse(response, input);
  }

  async addModel(input: ModelAddInput): Promise<ProviderModelItem> {
    const response = await this.client.request<RawModelWriteResponse>(
      MustangMethod.modelAdd,
      {
        providerName: input.providerName,
        providerType: input.providerType ?? undefined,
        apiKey: input.apiKey ?? undefined,
        baseUrl: input.baseUrl ?? undefined,
        awsSecretKey: input.awsSecretKey ?? undefined,
        awsRegion: input.awsRegion ?? undefined,
        modelId: input.modelId,
        displayName: input.displayName ?? null,
        contextWindow: input.contextWindow ?? null,
        roles: input.roles,
      },
      { timeoutMs: 10_000 },
    );
    return mapModelWriteResponse(response, input);
  }
}

interface RawModelWriteResponse {
  model?: unknown;
  providerType?: unknown;
  provider_type?: unknown;
  baseUrl?: unknown;
  base_url?: unknown;
  effectiveBaseUrl?: unknown;
  effective_base_url?: unknown;
  awsRegion?: unknown;
  aws_region?: unknown;
  hasApiKey?: unknown;
  has_api_key?: unknown;
  apiKeyDisplay?: unknown;
  api_key_display?: unknown;
  hasAwsSecretKey?: unknown;
  has_aws_secret_key?: unknown;
  awsSecretKeyDisplay?: unknown;
  aws_secret_key_display?: unknown;
  settingFields?: unknown;
  setting_fields?: unknown;
  displayName?: unknown;
  display_name?: unknown;
  contextWindow?: unknown;
  context_window?: unknown;
  roles?: unknown;
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

function stringOrNull(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const text = String(value).trim();
  return text || null;
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
  const baseUrl = stringOrNull(raw.baseUrl ?? raw.base_url);
  const effectiveBaseUrl = stringOrNull(raw.effectiveBaseUrl ?? raw.effective_base_url);
  const awsRegion = stringOrNull(raw.awsRegion ?? raw.aws_region);
  const hasApiKey = Boolean(raw.hasApiKey ?? raw.has_api_key);
  const apiKeyDisplay = stringOrNull(raw.apiKeyDisplay ?? raw.api_key_display);
  const hasAwsSecretKey = Boolean(raw.hasAwsSecretKey ?? raw.has_aws_secret_key);
  const awsSecretKeyDisplay = stringOrNull(raw.awsSecretKeyDisplay ?? raw.aws_secret_key_display);
  const settingFields = stringList(raw.settingFields ?? raw.setting_fields);
  const models = Array.isArray(raw.models) ? raw.models.map(item => String(item)).filter(Boolean) : [];
  const contextWindows = mapContextWindows(raw.contextWindows ?? raw.context_windows);
  const displayNames = mapStringMap(raw.displayNames ?? raw.display_names);
  const roles = isRecord(raw.roles)
    ? Object.fromEntries(Object.entries(raw.roles).map(([key, value]) => [key, Boolean(value)]))
    : {};
  if (!name || !providerType) return null;
  return { name, providerType, baseUrl, effectiveBaseUrl, awsRegion, hasApiKey, apiKeyDisplay, hasAwsSecretKey, awsSecretKeyDisplay, settingFields, models, contextWindows, displayNames, roles };
}

function mapProviderTypeOptions(value: unknown, providers: ModelProviderInfo[]): ProviderTypeInfo[] {
  const byType = new Map<string, ProviderTypeInfo>();
  if (Array.isArray(value)) {
    for (const raw of value) {
      if (!isRecord(raw)) continue;
      const providerType = String(raw.providerType ?? raw.provider_type ?? "").trim();
      if (!providerType || byType.has(providerType)) continue;
      byType.set(providerType, {
        providerType,
        settingFields: stringList(raw.settingFields ?? raw.setting_fields),
        effectiveBaseUrl: stringOrNull(raw.effectiveBaseUrl ?? raw.effective_base_url),
      });
    }
  }
  for (const provider of providers) {
    if (byType.has(provider.providerType)) continue;
    byType.set(provider.providerType, {
      providerType: provider.providerType,
      settingFields: provider.settingFields,
      effectiveBaseUrl: provider.effectiveBaseUrl,
    });
  }
  return [...byType.values()].sort((a, b) => a.providerType.localeCompare(b.providerType));
}

function mapModelWriteResponse(response: RawModelWriteResponse, input: ModelAddInput | ModelUpdateInput): ProviderModelItem {
  const ref = Array.isArray(response.model) ? response.model : [input.providerName, input.modelId];
  const providerName = String(ref[0] ?? input.providerName);
  const modelId = String(ref[1] ?? input.modelId);
  const displayName = String(response.displayName ?? response.display_name ?? input.displayName ?? "");
  return {
    displayName: displayName || modelId,
    providerName,
    providerType: String(response.providerType ?? response.provider_type ?? input.providerType ?? ""),
    providerBaseUrl: stringOrNull(response.baseUrl ?? response.base_url),
    providerEffectiveBaseUrl: stringOrNull(response.effectiveBaseUrl ?? response.effective_base_url),
    providerAwsRegion: stringOrNull(response.awsRegion ?? response.aws_region),
    providerHasApiKey: Boolean(response.hasApiKey ?? response.has_api_key),
    providerApiKeyDisplay: stringOrNull(response.apiKeyDisplay ?? response.api_key_display),
    providerHasAwsSecretKey: Boolean(response.hasAwsSecretKey ?? response.has_aws_secret_key),
    providerAwsSecretKeyDisplay: stringOrNull(response.awsSecretKeyDisplay ?? response.aws_secret_key_display),
    providerSettingFields: stringList(response.settingFields ?? response.setting_fields),
    modelId,
    roles: Array.isArray(response.roles) ? response.roles.map(role => String(role)).filter(Boolean).sort() : (input.roles ?? []),
    contextWindow: numberOrNull(response.contextWindow ?? response.context_window) ?? input.contextWindow ?? null,
  };
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(item => String(item).trim()).filter(Boolean);
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

function mapStringMap(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {};
  const result: Record<string, string> = {};
  for (const [key, raw] of Object.entries(value)) {
    const text = String(raw ?? "").trim();
    if (text) result[key] = text;
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
