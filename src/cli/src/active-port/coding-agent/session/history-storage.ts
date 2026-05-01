// -nocheck
export interface HistoryEntry {
	prompt: string;
	text: string;
	timestamp?: number;
}

export class HistoryStorage {
	#entries: HistoryEntry[] = [];

	static open(): HistoryStorage {
		return new HistoryStorage();
	}

	async add(text: string): Promise<void> {
		this.#entries.push({ prompt: text, text, timestamp: Date.now() });
	}

	all(): HistoryEntry[] {
		return [...this.#entries];
	}

	search(query: string, limit = 100): HistoryEntry[] {
		return this.#entries.filter(entry => entry.text.includes(query) || entry.prompt.includes(query)).slice(-limit).reverse();
	}

	getRecent(limit = 100): HistoryEntry[] {
		return this.#entries.slice(-limit).reverse();
	}

	close(): void {}
}
