// @ts-nocheck
export class SessionObserverRegistry {
	#listeners = new Set<() => void>();
	#mainSession: string | undefined;

	constructor(..._args: unknown[]) {}
	dispose(): void {
		this.#listeners.clear();
	}
	add(..._args: unknown[]): void {
		this.#emit();
	}
	remove(..._args: unknown[]): void {
		this.#emit();
	}
	subscribeToEventBus(..._args: unknown[]): void {}
	setMainSession(session: string | undefined): void {
		this.#mainSession = session;
		this.#emit();
	}
	getActiveSubagentCount(): number {
		return 0;
	}
	getSessions(): unknown[] {
		return this.#mainSession ? [{ id: this.#mainSession, type: "main" }] : [];
	}
	resetSessions(): void {
		this.#mainSession = undefined;
		this.#emit();
	}
	onChange(listener: () => void): () => void {
		this.#listeners.add(listener);
		return () => this.#listeners.delete(listener);
	}
	#emit(): void {
		for (const listener of this.#listeners) listener();
	}
}
