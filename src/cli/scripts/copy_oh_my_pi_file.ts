import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { dirname, isAbsolute, relative, resolve } from "path";

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
}

const cliRoot = resolve(import.meta.dir, "..");
const repoRoot = resolve(cliRoot, "..", "..");
const manifestPath = resolve(cliRoot, "active-port-manifest.json");

function usage(): never {
  console.error(
    "Usage: bun run src/cli/scripts/copy_oh_my_pi_file.ts <upstream-relative> <phase> [notes]",
  );
  process.exit(1);
}

function fail(message: string): never {
  console.error(`FAIL: ${message}`);
  process.exit(1);
}

function isInside(child: string, parent: string): boolean {
  const rel = relative(parent, child);
  return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
}

function assertRelativePath(path: string, label: string): void {
  if (!path || path.startsWith("/") || path.split(/[\\/]/).includes("..")) {
    fail(`${label} must be a non-empty relative path that does not escape its root: ${path}`);
  }
}

function mapTarget(upstreamRelative: string): string {
  const tuiPrefix = "packages/tui/src/";
  const codingAgentPrefix = "packages/coding-agent/src/";
  if (upstreamRelative.startsWith(tuiPrefix)) {
    return `src/cli/src/active-port/tui/${upstreamRelative.slice(tuiPrefix.length)}`;
  }
  if (upstreamRelative.startsWith(codingAgentPrefix)) {
    return `src/cli/src/active-port/coding-agent/${upstreamRelative.slice(codingAgentPrefix.length)}`;
  }
  fail(
    `upstream path must be under ${tuiPrefix} or ${codingAgentPrefix}: ${upstreamRelative}`,
  );
}

const [upstreamRelative, phase, notes] = process.argv.slice(2);
if (!upstreamRelative || !phase) usage();

assertRelativePath(upstreamRelative, "upstream-relative");

const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as Manifest;
const targetRelative = mapTarget(upstreamRelative);
const upstreamRoot = resolve(manifest.upstreamRoot);
const source = resolve(upstreamRoot, upstreamRelative);
const target = resolve(repoRoot, targetRelative);
const managedRoots = manifest.managedRoots.map((root) => resolve(repoRoot, root));

if (!existsSync(source)) fail(`upstream file does not exist: ${upstreamRelative}`);
if (!isInside(source, upstreamRoot)) fail(`upstream path escapes upstreamRoot: ${upstreamRelative}`);
if (!isInside(target, cliRoot)) fail(`target must stay inside src/cli: ${targetRelative}`);
if (!managedRoots.some((root) => isInside(target, root))) {
  fail(`target must live under a managedRoot from active-port-manifest.json: ${targetRelative}`);
}

mkdirSync(dirname(target), { recursive: true });
copyFileSync(source, target);

const existing = manifest.ports.find((entry) => entry.target === targetRelative);
if (existing) {
  existing.upstream = upstreamRelative;
  existing.phase = phase;
  existing.notes = notes;
} else {
  manifest.ports.push({ upstream: upstreamRelative, target: targetRelative, phase, notes });
}

manifest.ports.sort((left, right) => left.target.localeCompare(right.target));
writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);

console.log(`PASS: copied ${upstreamRelative} -> ${targetRelative}`);
