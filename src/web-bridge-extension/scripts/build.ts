import { cp, mkdir, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const dist = join(root, "dist", "chrome");

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
await cp(join(root, "manifest.chrome.json"), join(dist, "manifest.json"));
await cp(join(root, "src", "background.js"), join(dist, "background.js"));
await cp(join(root, "src", "content.js"), join(dist, "content.js"));
await cp(join(root, "src", "popup.html"), join(dist, "popup.html"));
await cp(join(root, "src", "popup.js"), join(dist, "popup.js"));
