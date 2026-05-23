import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "bun:test";

const root = join(import.meta.dir, "..");

describe("Chrome MV3 package", () => {
  test("declares the WebBridge service worker and popup", () => {
    const manifest = JSON.parse(readFileSync(join(root, "dist", "chrome", "manifest.json"), "utf8"));
    const packageJson = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
    expect(manifest.manifest_version).toBe(3);
    expect(manifest.version).toBe(packageJson.version);
    expect(manifest.background.service_worker).toBe("background.js");
    expect(manifest.action.default_popup).toBe("popup.html");
    expect(manifest.host_permissions).toContain("ws://127.0.0.1/*");
    expect(manifest.host_permissions).toContain("http://*/*");
  });
});
