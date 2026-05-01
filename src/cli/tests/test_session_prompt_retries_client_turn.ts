import { KernelDisconnected } from "../src/acp/client.js";
import { AcpMethod } from "../src/acp/methods.js";
import { MustangSession } from "../src/session.js";
import { assert } from "./helpers.js";

const calls: string[] = [];
const promptClientTurnIds: string[] = [];
let promptAttempts = 0;

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
    promptAttempts += 1;
    if (promptAttempts === 1) throw new KernelDisconnected("lost during prompt");
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
const result = await session.prompt("hello", () => {});

assert(result.stopReason === "end_turn", "prompt retry should return final result");
assert(calls[0] === AcpMethod.sessionResume, "prompt should resume before first send");
assert(calls[1] === AcpMethod.sessionPrompt, "prompt should send first prompt");
assert(calls[2] === AcpMethod.sessionResume, "prompt should resume again after reconnect");
assert(calls[3] === AcpMethod.sessionPrompt, "prompt should retry after reconnect");
assert(promptClientTurnIds.length === 2, "prompt should be attempted twice");
assert(
  promptClientTurnIds[0] === promptClientTurnIds[1] && promptClientTurnIds[0].length > 0,
  "retry should reuse the same client turn id",
);

console.log("PASS: session prompt retries with stable client turn id");
