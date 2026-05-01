import { resolveStartupSession } from "../src/startup/session-startup.js";
import { DEFAULT_CONFIG, type CliConfig } from "../src/config/schema.js";
import { assert } from "./helpers.js";

const requests: Array<{ method: string; params: any }> = [];
const client = {
  async request(method: string, params: any): Promise<any> {
    requests.push({ method, params });
    if (method === "session/list") return { sessions: [{ sessionId: "recent", title: "Recent", cwd: "/old" }], nextCursor: null };
    if (method === "session/load") return { sessionId: params.sessionId };
    if (method === "session/new") return { sessionId: "new-session" };
    throw new Error(method);
  },
  notify() {},
  promptRequest() { throw new Error("not used"); },
  executeShellRequest() { throw new Error("not used"); },
  executePythonRequest() { throw new Error("not used"); },
  onUpdate() { return () => {}; },
};

const service = {
  clientForSession: () => client,
  list: async (options: any) => {
    await client.request("session/list", options);
    return [{ sessionId: "recent", path: "recent", title: "Recent", cwd: "/old", updatedAt: null, createdAt: null, archivedAt: null, titleSource: null, totalInputTokens: null, totalOutputTokens: null, raw: { sessionId: "recent" } }];
  },
  load: async (sessionId: string, cwd: string) => client.request("session/load", { sessionId, cwd }),
  create: async (cwd: string) => client.request("session/new", { cwd, mcpServers: [] }),
} as any;

const config: CliConfig = { ...DEFAULT_CONFIG, kernel: { ...DEFAULT_CONFIG.kernel }, session: { ...DEFAULT_CONFIG.session }, ui: { ...DEFAULT_CONFIG.ui } };
let result = await resolveStartupSession(service, { newSession: false, print: false, help: false, sessionId: "explicit" }, config, { isInteractive: true, cwd: "/repo" });
assert(result.session.sessionId === "explicit", "--session should load explicit session");

result = await resolveStartupSession(service, { newSession: false, print: false, help: false }, config, { isInteractive: true, cwd: "/repo" });
assert(result.session.sessionId === "new-session", "default startup should create a new session");

result = await resolveStartupSession(service, { newSession: true, print: false, help: false }, config, { isInteractive: true, cwd: "/repo" });
assert(result.session.sessionId === "new-session", "--new should create a new session");

result = await resolveStartupSession(service, { newSession: false, print: true, help: false, prompt: "hello" }, config, { isInteractive: true, cwd: "/repo" });
assert(result.session.sessionId === "new-session", "--print/prompt should avoid picker and create new session");

config.session.startup = "last";
result = await resolveStartupSession(service, { newSession: false, print: false, help: false }, config, { isInteractive: true, cwd: "/repo" });
assert(result.session.sessionId === "recent", "last startup should load recent session");
assert(requests.some((call) => call.method === "session/load" && call.params.cwd === "/old"), "restore_cwd should load recent cwd");

console.log("PASS: session startup branches");
