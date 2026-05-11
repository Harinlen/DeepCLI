export interface WebFetchSetupLog {
	command?: string;
	exitCode?: number;
	stdout?: string;
	stderr?: string;
}

export interface WebFetchSetupResult {
	ok?: boolean;
	logs?: unknown[];
}

export function formatWebFetchSetupFailure(message?: string, setupResult?: WebFetchSetupResult | null): string {
	const logs = Array.isArray(setupResult?.logs) ? setupResult.logs : [];
	const failed = [...logs].reverse().find(isFailedLog) ?? logs[logs.length - 1];
	if (!isSetupLog(failed)) {
		return message || "WebFetch backend setup failed with no setup logs.";
	}
	const command = failed.command || "unknown command";
	const exitCode = failed.exitCode ?? "unknown";
	const details = truncateTail((failed.stderr || failed.stdout || "no command output").trim(), 1200);
	const prefix = message && !message.endsWith(".") ? `${message}.` : (message || "WebFetch backend setup failed.");
	return `${prefix}\nCommand: ${command}\nExit code: ${exitCode}\n${details}`;
}

function isFailedLog(value: unknown): value is WebFetchSetupLog {
	return isSetupLog(value) && value.exitCode !== undefined && value.exitCode !== 0;
}

function isSetupLog(value: unknown): value is WebFetchSetupLog {
	return typeof value === "object" && value !== null;
}

function truncateTail(value: string, maxChars: number): string {
	if (value.length <= maxChars) return value;
	return `...${value.slice(-maxChars)}`;
}
