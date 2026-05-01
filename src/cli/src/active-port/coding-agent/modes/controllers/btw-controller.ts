// -nocheck
export class BtwController {
	constructor(..._args: unknown[]) {}
	dispose(): void {}
	start(..._args: unknown[]): Promise<unknown> {
		return Promise.resolve(undefined);
	}
	hasActiveRequest(): boolean {
		return false;
	}
	handleEscape(): boolean {
		return false;
	}
}
