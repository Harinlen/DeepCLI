// @ts-nocheck
export function resolveRoleModel() { return undefined; }
export function resolveModel() { return undefined; }
export function resolveModelRoleValue() { return undefined; }
export function resolveRoleSelection(_registry, _settings, role) { return { role: role ?? "default", model: undefined, explicitThinkingLevel: false }; }
export function formatModelSelectorValue(model) { return model?.name ?? model?.id ?? ""; }
