// @ts-nocheck
export function calculatePromptTokens(usage) {
  if (!usage) return 0;
  return Number(usage.input ?? 0) + Number(usage.output ?? 0) + Number(usage.cacheRead ?? 0) + Number(usage.cacheWrite ?? 0);
}
