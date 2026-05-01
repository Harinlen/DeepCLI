// @ts-nocheck

export interface SessionContext {
	id?: string;
	title?: string;
	path?: string;
	cwd?: string;
}

export interface SessionInfo extends SessionContext {
	path: string;
	id: string;
	cwd: string;
	title?: string;
	parentSessionPath?: string;
	created: Date;
	modified: Date;
	messageCount: number;
	firstMessage: string;
	allMessagesText: string;
}

export interface MustangSessionProvider {
	listSessions(cwd?: string, limit?: number): Promise<SessionInfo[]>;
}

let mustangSessionProvider: MustangSessionProvider | undefined;

export function setMustangSessionProvider(provider: MustangSessionProvider | undefined): void {
	mustangSessionProvider = provider;
}

export class SessionManager {
	static async list(cwd?: string, _sessionDir?: string): Promise<SessionInfo[]> {
		return mustangSessionProvider?.listSessions(cwd, 50) ?? [];
	}

	async getRecentSessions(): Promise<SessionInfo[]> {
		return SessionManager.list();
	}
}

export async function getRecentSessions(): Promise<SessionInfo[]> {
	const sessions = await SessionManager.list();
	return sessions.slice(0, 5).map(session => ({
		...session,
		name: session.title || session.firstMessage || session.id,
		timeAgo: formatTimeAgo(session.modified),
	}));
}

function formatTimeAgo(date: Date): string {
	const diffMs = Date.now() - date.getTime();
	const mins = Math.floor(diffMs / 60000);
	if (mins < 1) return "just now";
	if (mins < 60) return `${mins}m ago`;
	const hours = Math.floor(mins / 60);
	if (hours < 24) return `${hours}h ago`;
	const days = Math.floor(hours / 24);
	return `${days}d ago`;
}
