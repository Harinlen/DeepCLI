import { MustangMethod } from "@/acp/methods.js";

export interface ModelProfile {
  name: string;
  providerType: string;
  modelId: string;
  contextWindow?: number | null;
  isDefault: boolean;
}

export interface ModelState {
  profiles: ModelProfile[];
  defaultModel: string;
}

export interface ModelServiceClient {
  request<R = unknown>(method: string, params: unknown, opts?: { timeoutMs?: number }): Promise<R>;
}

interface RawProfile {
  name?: unknown;
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
    const response = await this.client.request<{ defaultModel?: unknown; default_model?: unknown }>(
      MustangMethod.modelSetDefault,
      {
        provider: profile.providerType,
        model: profile.modelId,
      },
    );
    const value = response.defaultModel ?? response.default_model;
    return Array.isArray(value) ? value.join("/") : String(value ?? profile.name);
  }
}

function mapProfile(raw: RawProfile): ModelProfile | null {
  const name = String(raw.name ?? "");
  const providerType = String(raw.providerType ?? raw.provider_type ?? "");
  const modelId = String(raw.modelId ?? raw.model_id ?? "");
  if (!name || !providerType || !modelId) return null;
  return {
    name,
    providerType,
    modelId,
    contextWindow: numberOrNull(raw.contextWindow ?? raw.context_window),
    isDefault: Boolean(raw.isDefault ?? raw.is_default),
  };
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
