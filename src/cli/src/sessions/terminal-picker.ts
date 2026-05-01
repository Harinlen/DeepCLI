import { createInterface } from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import type { CliSessionInfo } from "@/sessions/types.js";
import { pickSessionByNumber, renderSessionPicker, createPickerState } from "@/sessions/picker.js";

export async function promptForSessionSelection(sessions: CliSessionInfo[]): Promise<CliSessionInfo | "new" | "cancel"> {
  if (sessions.length === 0) return "new";
  output.write(`${renderSessionPicker(createPickerState(sessions, Math.min(10, sessions.length)))}\n`);
  const rl = createInterface({ input, output });
  try {
    while (true) {
      const answer = await rl.question("Select session number, n for new, q to cancel: ");
      const picked = pickSessionByNumber(sessions, answer);
      if (picked) return picked;
      output.write("Invalid selection.\n");
    }
  } finally {
    rl.close();
  }
}

