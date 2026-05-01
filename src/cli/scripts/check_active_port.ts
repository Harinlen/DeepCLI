import { existsSync, readdirSync, readFileSync, statSync } from "fs";
import { isAbsolute, relative, resolve } from "path";

interface PortEntry {
  upstream: string;
  target: string;
  phase: string;
  notes?: string;
}

interface Manifest {
  version: number;
  upstreamRoot: string;
  managedRoots: string[];
  bulkVendorDenylist: string[];
  ports: PortEntry[];
  localAssets?: string[];
}

const cliRoot = resolve(import.meta.dir, "..");
const repoRoot = resolve(cliRoot, "..", "..");
const manifestPath = resolve(cliRoot, "active-port-manifest.json");
const tsconfigPath = resolve(cliRoot, "tsconfig.json");

function fail(message: string): never {
  console.error(`FAIL: ${message}`);
  process.exit(1);
}

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) fail(message);
}

function readJson<T>(path: string): T {
  try {
    return JSON.parse(readFileSync(path, "utf8")) as T;
  } catch (error) {
    fail(`${path} is not readable JSON: ${String(error)}`);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertRelativePath(path: string, field: string): void {
  assert(path.length > 0, `${field} must not be empty`);
  assert(!path.startsWith("/"), `${field} must be repo-relative: ${path}`);
  assert(!path.split(/[\\/]/).includes(".."), `${field} must not escape root: ${path}`);
}

function isInside(child: string, parent: string): boolean {
  const rel = relative(parent, child);
  return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
}

function assertManifestShape(raw: unknown): Manifest {
  assert(isRecord(raw), "manifest must be an object");
  assert(raw.version === 1, "manifest version must be 1");
  assert(typeof raw.upstreamRoot === "string", "upstreamRoot must be a string");
  assert(Array.isArray(raw.managedRoots), "managedRoots must be an array");
  assert(Array.isArray(raw.bulkVendorDenylist), "bulkVendorDenylist must be an array");
  assert(Array.isArray(raw.ports), "ports must be an array");
  if (raw.localAssets !== undefined) {
    assert(Array.isArray(raw.localAssets), "localAssets must be an array when present");
  }

  for (const root of raw.managedRoots) {
    assert(typeof root === "string", "managedRoots entries must be strings");
    assertRelativePath(root, "managedRoots entry");
  }

  for (const denied of raw.bulkVendorDenylist) {
    assert(typeof denied === "string", "bulkVendorDenylist entries must be strings");
    assertRelativePath(denied, "bulkVendorDenylist entry");
  }

  for (const entry of raw.ports) {
    assert(isRecord(entry), "ports entries must be objects");
    assert(typeof entry.upstream === "string", "port.upstream must be a string");
    assert(typeof entry.target === "string", "port.target must be a string");
    assert(typeof entry.phase === "string", "port.phase must be a string");
    assertRelativePath(entry.upstream, "port.upstream");
    assertRelativePath(entry.target, "port.target");
  }

  for (const asset of raw.localAssets ?? []) {
    assert(typeof asset === "string", "localAssets entries must be strings");
    assertRelativePath(asset, "localAssets entry");
  }

  return raw as unknown as Manifest;
}

function checkTsconfigBoundary(): void {
  const raw = readJson<Record<string, unknown>>(tsconfigPath);
  const include = raw.include;
  assert(Array.isArray(include), "tsconfig include must be an array");
  const expected = ["src/**/*", "tests/**/*"];
  assert(
    include.length === expected.length && expected.every((value, index) => include[index] === value),
    `tsconfig include must stay exactly ${JSON.stringify(expected)}`,
  );
}

function checkBulkVendorDenylist(manifest: Manifest): void {
  for (const denied of manifest.bulkVendorDenylist) {
    const absolute = resolve(repoRoot, denied);
    assert(!existsSync(absolute), `bulk vendor directory is not allowed: ${denied}`);
  }
}

function expectedTargetFor(upstream: string): string {
  const tuiPrefix = "packages/tui/src/";
  const codingAgentPrefix = "packages/coding-agent/src/";
  if (upstream.startsWith(tuiPrefix)) {
    return `src/cli/src/active-port/tui/${upstream.slice(tuiPrefix.length)}`;
  }
  if (upstream.startsWith(codingAgentPrefix)) {
    return `src/cli/src/active-port/coding-agent/${upstream.slice(codingAgentPrefix.length)}`;
  }
  fail(`port.upstream must be under packages/tui/src or packages/coding-agent/src: ${upstream}`);
}

function checkManagedPorts(manifest: Manifest): void {
  const upstreamRoot = resolve(manifest.upstreamRoot);
  assert(existsSync(upstreamRoot), `upstreamRoot does not exist: ${manifest.upstreamRoot}`);

  const managedRoots = manifest.managedRoots.map((root) => resolve(repoRoot, root));
  const seenTargets = new Set<string>();

  for (const entry of manifest.ports) {
    const upstream = resolve(upstreamRoot, entry.upstream);
    const target = resolve(repoRoot, entry.target);
    const expectedTarget = expectedTargetFor(entry.upstream);

    assert(isInside(upstream, upstreamRoot), `port.upstream escapes upstreamRoot: ${entry.upstream}`);
    assert(
      entry.target === expectedTarget,
      `port.target must preserve upstream relative structure: ${entry.upstream} -> ${expectedTarget}, got ${entry.target}`,
    );
    assert(isInside(target, cliRoot), `port.target must stay inside src/cli: ${entry.target}`);
    assert(
      managedRoots.some((root) => isInside(target, root)),
      `port.target must live under a managedRoot: ${entry.target}`,
    );
    assert(existsSync(upstream), `port.upstream does not exist: ${entry.upstream}`);
    assert(existsSync(target), `port.target does not exist: ${entry.target}`);
    assert(statSync(target).isFile(), `port.target must be a file: ${entry.target}`);
    assert(!seenTargets.has(entry.target), `duplicate port.target: ${entry.target}`);
    seenTargets.add(entry.target);
  }
}

function collectFiles(root: string): string[] {
  if (!existsSync(root)) return [];
  const entries = readdirSync(root, { withFileTypes: true });
  const files: string[] = [];
  for (const entry of entries) {
    const absolute = resolve(root, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectFiles(absolute));
    } else if (entry.isFile()) {
      files.push(absolute);
    }
  }
  return files;
}

function checkNoUnregisteredManagedFiles(manifest: Manifest): void {
  const registered = new Set([
    ...manifest.ports.map(entry => resolve(repoRoot, entry.target)),
    ...(manifest.localAssets ?? []).map(asset => resolve(repoRoot, asset)),
  ]);
  const managedRoots = manifest.managedRoots.map((root) => resolve(repoRoot, root));
  for (const asset of manifest.localAssets ?? []) {
    const absolute = resolve(repoRoot, asset);
    assert(isInside(absolute, cliRoot), `localAssets entry must stay inside src/cli: ${asset}`);
    assert(
      managedRoots.some((root) => isInside(absolute, root)),
      `localAssets entry must live under a managedRoot: ${asset}`,
    );
    assert(existsSync(absolute), `localAssets entry does not exist: ${asset}`);
    assert(statSync(absolute).isFile(), `localAssets entry must be a file: ${asset}`);
  }
  for (const root of manifest.managedRoots) {
    const absoluteRoot = resolve(repoRoot, root);
    for (const file of collectFiles(absoluteRoot)) {
      assert(
        registered.has(file),
        `managed file is not registered in active-port-manifest.json: ${relative(repoRoot, file)}`,
      );
    }
  }
}

const manifest = assertManifestShape(readJson<unknown>(manifestPath));

checkTsconfigBoundary();
checkBulkVendorDenylist(manifest);
checkManagedPorts(manifest);
checkNoUnregisteredManagedFiles(manifest);

console.log(`PASS: active-port manifest v${manifest.version} (${manifest.ports.length} files)`);
