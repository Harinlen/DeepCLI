// seam 3: session/prompt streams agent_message_chunk, returns PromptResult
import { connect, assert } from "./helpers.js";
import { MustangSession } from "../src/session.js";

const client = await connect();

// Auto-allow all permission requests
client.setPermissionHandler(async (_id, _req) => ({
  outcome: { outcome: "selected", optionId: "allow_once" },
}));

const session = await MustangSession.create(client);
const chunks: string[] = [];

const result = await session.prompt(
  "respond with exactly the words: hello world",
  (update) => {
    if (update.sessionUpdate === "agent_message_chunk") {
      const content = update.content as { text?: string } | undefined;
      if (content?.text) chunks.push(content.text);
    }
  },
);

assert(chunks.length > 0, `expected agent_message_chunk events, got 0`);
assert(
  result.stopReason !== undefined,
  `PromptResult.stopReason missing (got ${JSON.stringify(result)})`,
);

await client.close();
console.log(
  `PASS: prompt → ${chunks.length} chunks, stopReason=${result.stopReason}`,
);
