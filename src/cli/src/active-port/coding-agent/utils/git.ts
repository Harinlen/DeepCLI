// @ts-nocheck
export const repo = { resolveSync: (_cwd?: string) => null };
export const head = { resolveSync: (_cwd?: string) => null };
export const branch = { default: async (_cwd?: string) => undefined };
export const status = { summary: async (_cwd?: string) => ({ staged: 0, unstaged: 0, untracked: 0 }) };
