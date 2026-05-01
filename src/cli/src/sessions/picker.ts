import type { CliSessionInfo } from "@/sessions/types.js";

export interface SessionPickerState {
  sessions: CliSessionInfo[];
  query: string;
  selected: number;
  pageSize: number;
}

export function createPickerState(sessions: CliSessionInfo[], pageSize = 10): SessionPickerState {
  return { sessions, query: "", selected: 0, pageSize };
}

export function filterSessions(state: SessionPickerState): CliSessionInfo[] {
  const query = state.query.trim().toLowerCase();
  if (!query) return state.sessions;
  return state.sessions.filter((session) => {
    const haystack = `${session.title} ${session.cwd} ${session.sessionId}`.toLowerCase();
    let index = 0;
    for (const ch of query) {
      index = haystack.indexOf(ch, index);
      if (index === -1) return false;
      index++;
    }
    return true;
  });
}

export function movePickerSelection(state: SessionPickerState, delta: number): void {
  const count = filterSessions(state).length;
  if (count === 0) {
    state.selected = 0;
    return;
  }
  state.selected = Math.max(0, Math.min(count - 1, state.selected + delta));
}

export function renderSessionPicker(state: SessionPickerState): string {
  const filtered = filterSessions(state);
  if (filtered.length === 0) return "No sessions. Press n to create a new session.";
  const start = Math.floor(state.selected / state.pageSize) * state.pageSize;
  const page = filtered.slice(start, start + state.pageSize);
  return page.map((session, offset) => {
    const index = start + offset;
    const marker = index === state.selected ? ">" : " ";
    const archived = session.archivedAt ? " [archived]" : "";
    const cwd = session.cwd ? ` — ${session.cwd}` : "";
    return `${marker} ${index + 1}. ${session.title}${archived}${cwd}`;
  }).join("\n");
}

export function pickSessionByNumber(sessions: CliSessionInfo[], input: string): CliSessionInfo | "new" | "cancel" | null {
  const normalized = input.trim().toLowerCase();
  if (normalized === "n" || normalized === "new") return "new";
  if (normalized === "q" || normalized === "esc" || normalized === "cancel") return "cancel";
  const index = Number(normalized);
  if (Number.isInteger(index) && index >= 1 && index <= sessions.length) return sessions[index - 1];
  return null;
}

