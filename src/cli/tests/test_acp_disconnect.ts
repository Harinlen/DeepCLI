import { WebSocketServer, type WebSocket } from "ws";
import { AcpClient, KernelDisconnected } from "../src/acp/client.js";
import { assert } from "./helpers.js";

const server = new WebSocketServer({ port: 0 });
if (server.address() === null) {
  await new Promise<void>((resolve) => server.once("listening", resolve));
}
const port = (server.address() as { port: number }).port;
let sawPendingRequest!: () => void;
const pendingRequestSeen = new Promise<void>((resolve) => {
  sawPendingRequest = resolve;
});

server.on("connection", (socket) => {
  socket.on("message", (raw) => {
    const msg = JSON.parse(raw.toString()) as Record<string, unknown>;
    if (msg.method === "initialize") {
      socket.send(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: {} }));
    } else if (msg.method === "session/list") {
      sawPendingRequest();
    }
    // Deliberately do not answer other requests; the close path must reject them.
  });
});

const client = await AcpClient.connect(`ws://127.0.0.1:${port}`, "dev");
let disconnectMessage = "";
const states: string[] = [];
client.onDisconnect((error) => {
  disconnectMessage = error.message;
});
client.onConnectionStateChange((state) => {
  states.push(state);
});

const pending = client.request("session/list", {}, { timeoutMs: 0 });
await withTimeout(pendingRequestSeen, "server did not receive pending request");
(client as unknown as { ws: WebSocket }).ws.emit("close", 1006, Buffer.from(""));

let rejected: unknown;
try {
  await pending;
} catch (error) {
  rejected = error;
}

assert(rejected instanceof KernelDisconnected, "pending request should reject on websocket close");
assert(
  disconnectMessage.includes("Kernel connection lost"),
  `disconnect handler should receive a clear message, got ${disconnectMessage}`,
);
assert(states.includes("connecting"), "connection state should switch to connecting after websocket close");

client.close();
await new Promise<void>((resolve) => server.close(() => resolve()));
console.log("PASS: ACP disconnect rejects pending requests");

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
