import { DEFAULT_CONFIG } from "../src/config/schema.js";
import { applyThemeConfig } from "../src/startup/theme.js";
import { getCurrentThemeName } from "../src/active-port/coding-agent/modes/theme/theme.js";
import { assert } from "./helpers.js";

const dark = await applyThemeConfig({
  ...DEFAULT_CONFIG,
  kernel: { ...DEFAULT_CONFIG.kernel },
  session: { ...DEFAULT_CONFIG.session },
  ui: { ...DEFAULT_CONFIG.ui, theme: "dark", symbols: "unicode" },
});
assert(dark.active === "dark", "theme config should apply built-in dark theme");
assert(getCurrentThemeName() === "dark", "theme singleton should reflect applied theme");

const fallback = await applyThemeConfig({
  ...DEFAULT_CONFIG,
  kernel: { ...DEFAULT_CONFIG.kernel },
  session: { ...DEFAULT_CONFIG.session },
  ui: { ...DEFAULT_CONFIG.ui, theme: "missing-theme-name", symbols: "ascii" },
});
assert(fallback.active === "dark", "missing theme should fallback to dark");
assert(Boolean(fallback.warning), "missing theme should produce a warning");

console.log("PASS: theme config application");

