// @ts-nocheck
export const MODEL_ROLE_IDS = ["default"];
export const MODEL_ROLES = { default: { id: "default", label: "default", description: "Default model" } };
export function getKnownRoleIds() { return MODEL_ROLE_IDS; }
export function getRoleInfo(role) { return MODEL_ROLES[role] ?? { id: role, label: role, description: role }; }
export class ModelRegistry {
  list() { return []; }
  get() { return undefined; }
  getRoleModel() { return undefined; }
  resolveRoleModel() { return undefined; }
}
