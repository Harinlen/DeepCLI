import { WebSocketServer } from "ws";
import { assert } from "./helpers.js";
import { AcpClient } from "../src/acp/client.js";

const server = new WebSocketServer({ port: 0 });
const port = (server.address() as { port: number }).port;
const seen: unknown[] = [];

server.on("connection", (socket) => {
  socket.on("message", (raw) => {
    const msg = JSON.parse(raw.toString()) as Record<string, unknown>;
    seen.push(msg);
    if (msg.method === "initialize") {
      socket.send(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: {} }));
      socket.send(JSON.stringify({
        jsonrpc: "2.0",
        id: 99,
        method: "session/request_permission",
        params: {
          sessionId: "s",
          toolCall: { toolCallId: "call-1", title: "Bash" },
          options: [
            { optionId: "allow_once", name: "Allow once", kind: "allow_once" },
            { optionId: "reject_once", name: "Reject", kind: "reject_once" },
          ],
          toolInput: { command: "rm -rf /tmp/nope" },
        },
      }));
    }
  });
});

const client = await AcpClient.connect(`ws://127.0.0.1:${port}`, "dev");
await new Promise((resolve) => setTimeout(resolve, 50));
client.close();
server.close();

const response = seen.find((msg) => {
  const frame = msg as Record<string, unknown>;
  return frame.id === 99 && "result" in frame;
}) as { result?: { outcome?: { outcome?: string; optionId?: string } } } | undefined;

assert(Boolean(response), "client should respond to kernel-initiated permission request");
assert(response?.result?.outcome?.outcome === "selected", "default no-handler path should select fail-closed reject");
assert(response?.result?.outcome?.optionId === "reject_once", "fail-closed should choose available reject option");

console.log("PASS: ACP permission response");
