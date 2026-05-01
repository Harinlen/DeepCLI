// -nocheck
export type ToolRenderer = (input: unknown) => string | string[];
export const toolRenderers = new Map<string, ToolRenderer>();
