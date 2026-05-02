import { AcpError } from "../src/acp/client.js";
import { AcpMethod } from "../src/acp/methods.js";
import { MustangSession } from "../src/session.js";
import { assert } from "./helpers.js";

const calls: string[] = [];
let resumeAttempts = 0;

const client = {
  async request(method: string) {
    calls.push(method);
    if (method === AcpMethod.sessionResume) {
      resumeAttempts += 1;
      if (resumeAttempts < 3) throw new AcpError(-32603, "Internal error");
    }
    return {};
  },
  notify() {},
  onUpdate() {
    return () => {};
  },
  async promptRequest() {
    calls.push(AcpMethod.sessionPrompt);
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

assert(result.stopReason === "end_turn", "prompt should succeed after transient resume failures");
assert(resumeAttempts === 3, `resume should retry transient internal errors, got ${resumeAttempts}`);
assert(calls.at(-1) === AcpMethod.sessionPrompt, "prompt should be sent only after resume succeeds");

console.log("PASS: session resume retries transient internal errors");
