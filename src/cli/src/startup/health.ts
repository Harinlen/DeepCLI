export interface KernelHealth {
  name?: string;
  version?: string;
  boot_time?: number;
}

export async function fetchKernelHealth(url: string): Promise<KernelHealth | null> {
  try {
    const response = await fetch(url);
    if (!response.ok) return null;
    const payload = await response.json() as unknown;
    if (!payload || typeof payload !== "object") return null;
    return payload as KernelHealth;
  } catch {
    return null;
  }
}

export async function fetchKernelVersion(url: string): Promise<string | null> {
  const health = await fetchKernelHealth(url);
  return typeof health?.version === "string" && health.version.trim()
    ? health.version.trim()
    : null;
}
