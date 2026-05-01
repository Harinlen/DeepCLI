import type { CliConfig } from "@/config/schema.js";
import {
  getAvailableThemes,
  getCurrentThemeName,
  initTheme,
  isValidSymbolPreset,
  setTheme,
  type SymbolPreset,
} from "@/active-port/coding-agent/modes/theme/theme.js";

export interface AppliedTheme {
  requested: string;
  active: string;
  warning?: string;
}

export async function applyThemeConfig(config: CliConfig): Promise<AppliedTheme> {
  const requested = config.ui.theme || "dark";
  const available = await getAvailableThemes();
  const active = available.includes(requested) ? requested : "dark";
  const symbolPreset: SymbolPreset | undefined = isValidSymbolPreset(config.ui.symbols)
    ? config.ui.symbols
    : "unicode";

  await initTheme(false, symbolPreset, false, config.ui.auto_theme ? "dark" : active, config.ui.auto_theme ? "light" : active);
  if (!config.ui.auto_theme && active !== requested) {
    await setTheme(active, false);
  }
  return {
    requested,
    active: getCurrentThemeName() ?? active,
    warning: active !== requested ? `Theme "${requested}" not found; using "${active}".` : undefined,
  };
}

