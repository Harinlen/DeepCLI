// -nocheck
export interface ExtensionManager {
	load?(): Promise<void>;
}

export async function loadExtensions(): Promise<ExtensionManager[]> {
	return [];
}

export function createExtensionManager(): ExtensionManager {
	return {};
}
