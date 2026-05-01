// seam 2: session/new returns a valid sessionId
import { connect, assert } from "./helpers.js";

const client = await connect();

const result = await client.request<{ sessionId: string }>("session/new", {
  cwd: process.cwd(),
  mcpServers: [],
});

assert(typeof result.sessionId === "string", "sessionId should be a string");
assert(result.sessionId.length > 0, "sessionId should be non-empty");

await client.close();
console.log(`PASS: session/new → sessionId = ${result.sessionId}`);
