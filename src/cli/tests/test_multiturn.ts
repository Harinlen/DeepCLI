// seam 4: multi-turn context is preserved within a session
import { connect, assert } from "./helpers.js";
import { MustangSession } from "../src/session.js";

const client = await connect();

client.setPermissionHandler(async (_id, _req) => ({
  outcome: { outcome: "selected", optionId: "allow_once" },
}));

const session = await MustangSession.create(client);

const collect = () => {
  const parts: string[] = [];
  const handler = (update: { sessionUpdate: string; content?: unknown }) => {
    if (update.sessionUpdate === "agent_message_chunk") {
      const content = update.content as { text?: string } | undefined;
      if (content?.text) parts.push(content.text);
    }
  };
  return { parts, handler };
};

// Turn 1: plant a word
const { handler: h1 } = collect();
await session.prompt("remember the word: ZEPHYR", h1);

// Turn 2: ask the model to recall it
const { parts: parts2, handler: h2 } = collect();
await session.prompt("what word did I just ask you to remember?", h2);

const fullText = parts2.join("");
assert(
  fullText.toUpperCase().includes("ZEPHYR"),
  `expected ZEPHYR in response, got: ${fullText.slice(0, 200)}`,
);

await client.close();
console.log("PASS: multi-turn context preserved");
