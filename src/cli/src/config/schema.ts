export type SessionStartupMode = "picker" | "last" | "new";
export type SessionListScope = "cwd" | "all";
export type SymbolPresetName = "unicode" | "nerd" | "ascii";

export interface CliConfig {
  kernel: {
    url: string;
    token: string | null;
    token_file: string | null;
    autostart: boolean;
    autostart_command: string | null;
    health_url: string;
  };
  session: {
    startup: SessionStartupMode;
    list_scope: SessionListScope;
    include_archived: boolean;
    picker_limit: number;
    restore_cwd: boolean;
  };
  ui: {
    theme: string;
    auto_theme: boolean;
    symbols: SymbolPresetName;
    status_line: boolean;
    welcome_recent: number;
  };
}

export const DEFAULT_CONFIG: CliConfig = {
  kernel: {
    url: "ws://localhost:8200",
    token: null,
    token_file: "~/.mustang/state/auth_token",
    autostart: false,
    autostart_command: null,
    health_url: "http://localhost:8200/",
  },
  session: {
    startup: "new",
    list_scope: "cwd",
    include_archived: false,
    picker_limit: 50,
    restore_cwd: true,
  },
  ui: {
    theme: "dark",
    auto_theme: false,
    symbols: "unicode",
    status_line: true,
    welcome_recent: 3,
  },
};

export function cloneDefaultConfig(): CliConfig {
  return {
    kernel: { ...DEFAULT_CONFIG.kernel },
    session: { ...DEFAULT_CONFIG.session },
    ui: { ...DEFAULT_CONFIG.ui },
  };
}
