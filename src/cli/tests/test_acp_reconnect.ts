import { WebSocketServer, type WebSocket } from "ws";
import { AcpClient, KernelDisconnected } from "../src/acp/client.js";
import { assert } from "./helpers.js";

const server = new WebSocketServer({ port: 0 });
if (server.address() === null) {
  await new Promise<void>((resolve) => server.once("listening", resolve));
}
const port = (server.address() as { port: number }).port;

let firstListSeen!: () => void;
const firstListReceived = new Promise<void>((resolve) => {
  firstListSeen = resolve;
});
let listCount = 0;
const sockets = new Set<WebSocket>();

server.on("connection", (socket) => {
  sockets.add(socket);
  socket.on("close", () => sockets.delete(socket));
  socket.on("message", (raw) => {
    const msg = JSON.parse(raw.toString()) as Record<string, unknown>;
    if (msg.method === "initialize") {
      socket.send(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: {} }));
    } else if (msg.method === "session/list") {
      listCount += 1;
      if (listCount === 1) {
        firstListSeen();
        return;
      }
      socket.send(JSON.stringify({
        jsonrpc: "2.0",
        id: msg.id,
        result: { sessions: [], nextCursor: null },
      }));
    }
  });
});

const client = await AcpClient.connect(`ws://127.0.0.1:${port}`, "dev");
let disconnected = false;
let reconnected = false;
client.onDisconnect(() => {
  disconnected = true;
});
client.onReconnect(() => {
  reconnected = true;
});

const pending = client.request("session/list", {}, { timeoutMs: 0 });
await withTimeout(firstListReceived, "server did not receive initial request");
(client as unknown as { ws: WebSocket }).ws.emit("close", 1006, Buffer.from(""));

let rejected: unknown;
try {
  await withTimeout(pending, "in-flight request did not reject on disconnect");
} catch (error) {
  rejected = error;
}
assert(rejected instanceof KernelDisconnected, "in-flight request should reject before reconnect");
assert(disconnected, "disconnect handler should fire");

const result = await withTimeout(
  client.request<{ sessions: unknown[]; nextCursor: string | null }>(
    "session/list",
    {},
  ),
  "request after reconnect did not complete",
);
assert(Array.isArray(result.sessions), "request after reconnect should complete");
assert(reconnected, "reconnect handler should fire");

client.close();
for (const socket of sockets) socket.terminate();
server.close();
console.log("PASS: ACP reconnect restores future requests");

function withTimeout<T>(promise: Promise<T>, message: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), 2_000);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}
