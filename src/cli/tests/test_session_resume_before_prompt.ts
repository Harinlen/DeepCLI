import { MustangSession } from "../src/session.js";
import { AcpMethod } from "../src/acp/methods.js";
import { assert } from "./helpers.js";

const calls: string[] = [];
const promptClientTurnIds: string[] = [];
const client = {
  async request(method: string) {
    calls.push(method);
    return {};
  },
  notify() {},
  onUpdate() {
    return () => {};
  },
  async promptRequest(_sessionId: string, _text: string, options: { clientTurnId?: string }) {
    calls.push(AcpMethod.sessionPrompt);
    promptClientTurnIds.push(options.clientTurnId ?? "");
    return { stopReason: "end_turn" };
  },
  async executeShellRequest() {
    throw new Error("not used");
  },
  async executePythonRequest() {
    throw new Error("not used");
  },
};

const session = new MustangSession(client as never, "sess-1");
await session.prompt("hello", () => {});

assert(calls[0] === AcpMethod.sessionResume, "prompt should rebind session before sending");
assert(calls[1] === AcpMethod.sessionPrompt, "prompt should send after session/resume");
assert(
  typeof promptClientTurnIds[0] === "string" && promptClientTurnIds[0].length > 0,
  "prompt should include a client turn id",
);

console.log("PASS: session prompt resumes before send");
