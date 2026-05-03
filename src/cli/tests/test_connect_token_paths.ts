import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DEFAULT_CONFIG } from "../src/config/schema.js";
import { resolveToken } from "../src/startup/connect.js";
import { assert } from "./helpers.js";

const dir = mkdtempSync(join(tmpdir(), "deepcli-cli-token-"));
try {
  const config = {
    ...DEFAULT_CONFIG,
    kernel: {
      ...DEFAULT_CONFIG.kernel,
      token: null,
      token_file: null,
    },
  };

  assert(
    resolveToken(config, { MUSTANG_TOKEN: "legacy-token", DEEPCLI_TOKEN: "deepcli-token" })
      === "deepcli-token",
    "DEEPCLI_TOKEN should take precedence over legacy MUSTANG_TOKEN",
  );

  const stateDir = join(dir, "state");
  mkdirSync(stateDir, { recursive: true });
  writeFileSync(join(stateDir, "auth_token"), "native-file-token\n");

  assert(
    resolveToken(config, { DEEPCLI_STATE_DIR: stateDir }) === "native-file-token",
    "native DeepCLI state token file should be used when no env token is set",
  );

  const explicitToken = join(dir, "explicit-token");
  writeFileSync(explicitToken, "explicit-file-token\n");
  const explicitConfig = {
    ...config,
    kernel: {
      ...config.kernel,
      token_file: explicitToken,
    },
  };
  assert(
    resolveToken(explicitConfig, { DEEPCLI_STATE_DIR: stateDir }) === "explicit-file-token",
    "explicit kernel.token_file should override default token file candidates",
  );
} finally {
  rmSync(dir, { recursive: true, force: true });
}

console.log("PASS: token env and native file path resolution");
