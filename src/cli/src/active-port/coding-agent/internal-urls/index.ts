// -nocheck
export function resolveLocalUrlToPath(url: string): string | null {
	if (url.startsWith("file://")) return new URL(url).pathname;
	return null;
}
