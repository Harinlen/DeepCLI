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

const modeCalls: string[] = [];
const modeClient = {
  async request(method: string, params: { modeId?: string }) {
    modeCalls.push(params.modeId ? `${method}:${params.modeId}` : method);
    if (method === AcpMethod.sessionResume) {
      return { modes: { currentModeId: "default" } };
    }
    return {};
  },
  notify() {},
  onUpdate() {
    return () => {};
  },
  async promptRequest() {
    modeCalls.push(AcpMethod.sessionPrompt);
    return { stopReason: "end_turn" };
  },
  async executeShellRequest() {
    throw new Error("not used");
  },
  async executePythonRequest() {
    throw new Error("not used");
  },
};

const modeSession = new MustangSession(modeClient as never, "sess-2");
await modeSession.prompt("hello", () => {}, { mode: "bypass" });

assert(
  modeCalls.join("|") === `${AcpMethod.sessionResume}|${AcpMethod.sessionSetMode}:bypass|${AcpMethod.sessionPrompt}`,
  `prompt should sync requested mode after resume and before send, got ${modeCalls.join("|")}`,
);

console.log("PASS: session prompt resumes before send");
