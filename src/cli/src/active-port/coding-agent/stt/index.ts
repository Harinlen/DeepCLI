// -nocheck
export interface SttState {
	status: "idle" | "recording" | "transcribing" | "error";
	message?: string;
}

export class STTController {
	state: SttState = { status: "idle" };
	start(): void {}
	stop(): void {}
	dispose(): void {}
}
