// -nocheck
export const LSP_STARTUP_EVENT_CHANNEL = "lsp:startup";

export interface LspStartupEvent {
	type?: string;
	server?: string;
	message?: string;
}
