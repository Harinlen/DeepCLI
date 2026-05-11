import { MustangMethod } from "@/acp/methods.js";

export interface WebFetchBackendOption {
  id: string;
  label: string;
  category: string;
  cost: string;
  role: string;
  status: string;
  installed: boolean;
  hasCredentials: boolean;
  available: boolean;
  setupRequired: boolean;
  setupPlan?: { backend?: string; commands?: string[]; reason?: string } | null;
  credentialRequired?: boolean;
  credentialRequest?: {
    backend?: string;
    kind?: string;
    label?: string;
    envKey?: string;
    secretName?: string;
    prompt?: string;
  } | null;
  current: boolean;
}

export interface WebFetchBackendState {
  current: string;
  options: WebFetchBackendOption[];
}

export interface SetWebFetchBackendResult {
  backend: string;
  changed: boolean;
  setupRequired?: boolean;
  setupPlan?: { backend?: string; commands?: string[]; reason?: string } | null;
  setupResult?: { ok?: boolean; logs?: unknown[] } | null;
  credentialRequired?: boolean;
  credentialRequest?: {
    backend?: string;
    kind?: string;
    label?: string;
    envKey?: string;
    secretName?: string;
    prompt?: string;
  } | null;
  message?: string | null;
}

export interface WebFetchConfigState {
  backend: string;
  backends: Record<string, Record<string, unknown>>;
}

export interface WebFetchServiceClient {
  request<R = unknown>(method: string, params: unknown, opts?: { timeoutMs?: number }): Promise<R>;
}

export class WebFetchService {
  constructor(private readonly client: WebFetchServiceClient) {}

  async backendOptions(): Promise<WebFetchBackendState> {
    const response = await this.client.request<WebFetchBackendState>(
      MustangMethod.webFetchBackendOptions,
      {},
      { timeoutMs: 10_000 },
    );
    return {
      current: String(response.current ?? "auto"),
      options: (response.options ?? []).map(mapBackendOption),
    };
  }

  async setBackend(backend: string, runSetup = false, apiKey?: string): Promise<SetWebFetchBackendResult> {
    return await this.client.request<SetWebFetchBackendResult>(
      MustangMethod.webFetchSetBackend,
      { backend, runSetup, apiKey },
      { timeoutMs: runSetup ? 10 * 60 * 1000 : 120_000 },
    );
  }

  async getConfig(): Promise<WebFetchConfigState> {
    const response = await this.client.request<WebFetchConfigState>(
      MustangMethod.webFetchGetConfig,
      {},
      { timeoutMs: 10_000 },
    );
    return {
      backend: String(response.backend ?? "auto"),
      backends: response.backends ?? {},
    };
  }

  async setConfig(path: string, value: unknown): Promise<WebFetchConfigState> {
    return await this.client.request<WebFetchConfigState>(
      MustangMethod.webFetchSetConfig,
      { path, value },
      { timeoutMs: 120_000 },
    );
  }
}

function mapBackendOption(raw: any): WebFetchBackendOption {
  return {
    id: String(raw.id ?? ""),
    label: String(raw.label ?? raw.id ?? ""),
    category: String(raw.category ?? ""),
    cost: String(raw.cost ?? ""),
    role: String(raw.role ?? ""),
    status: String(raw.status ?? ""),
    installed: Boolean(raw.installed),
    hasCredentials: Boolean(raw.hasCredentials ?? raw.has_credentials),
    available: Boolean(raw.available),
    setupRequired: Boolean(raw.setupRequired ?? raw.setup_required),
    setupPlan: raw.setupPlan ?? raw.setup_plan ?? null,
    credentialRequired: Boolean(raw.credentialRequired ?? raw.credential_required),
    credentialRequest: raw.credentialRequest ?? raw.credential_request ?? null,
    current: Boolean(raw.current),
  };
}
