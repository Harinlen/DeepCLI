import { WebSocketServer } from "ws";
import { AcpClient } from "../src/acp/client.js";
import { assert } from "./helpers.js";

const server = new WebSocketServer({ port: 0 });
if (server.address() === null) {
  await new Promise<void>((resolve) => server.once("listening", resolve));
}
const port = (server.address() as { port: number }).port;
const expectedTurnId = "11111111-1111-4111-8111-111111111111";
let sawTurnId = false;
let updateMeta: Record<string, unknown> | undefined;

server.on("connection", (socket) => {
  socket.on("message", (raw) => {
    const msg = JSON.parse(raw.toString()) as {
      id?: number;
      method?: string;
      params?: Record<string, unknown>;
    };
    if (msg.method === "initialize") {
      socket.send(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: {} }));
      return;
    }
    if (msg.method === "session/prompt") {
      const meta = msg.params?._meta as Record<string, unknown> | undefined;
      sawTurnId = meta?.["mustang.agent/clientTurnId"] === expectedTurnId;
      socket.send(
        JSON.stringify({
          jsonrpc: "2.0",
          method: "session/update",
          params: {
            sessionId: "sess-1",
            update: {
              sessionUpdate: "tool_call_update",
              toolCallId: "agent-1",
              status: "in_progress",
            },
            _meta: { "mustang.agent/agentStart": { agent_id: "a1" } },
          },
        }),
      );
      socket.send(
        JSON.stringify({
          jsonrpc: "2.0",
          id: msg.id,
          result: { stopReason: "end_turn" },
        }),
      );
    }
  });
});

const client = await AcpClient.connect(`ws://127.0.0.1:${port}`, "dev");
client.onUpdate((update) => {
  updateMeta = (update._meta ?? update.meta) as Record<string, unknown> | undefined;
});
await client.promptRequest("sess-1", "hello", { clientTurnId: expectedTurnId });

assert(sawTurnId, "session/prompt should send clientTurnId in ACP _meta");
assert(Boolean(updateMeta?.["mustang.agent/agentStart"]), "session/update should preserve ACP _meta on flattened update");

client.close();
await new Promise<void>((resolve) => server.close(() => resolve()));
console.log("PASS: ACP prompt sends client turn id");
