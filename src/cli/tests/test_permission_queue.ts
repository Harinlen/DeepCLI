import { assert } from "./helpers.js";
import { PermissionQueue } from "../src/permissions/queue.js";

const queue = new PermissionQueue();
const events: string[] = [];
let releaseFirst!: () => void;

const first = queue.enqueue(async () => {
  events.push("first:start");
  await new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });
  events.push("first:end");
  return "first";
});

const second = queue.enqueue(async () => {
  events.push("second:start");
  return "second";
});

await Promise.resolve();
assert(events.join(",") === "first:start", `second request started too early: ${events.join(",")}`);
releaseFirst();
const results = await Promise.all([first, second]);

assert(results.join(",") === "first,second", `unexpected queue results: ${results.join(",")}`);
assert(
  events.join(",") === "first:start,first:end,second:start",
  `permission queue did not serialize requests: ${events.join(",")}`,
);

console.log("PASS: permission queue");
