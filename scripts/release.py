#!/usr/bin/env python3
"""DeepCLI release branch helper.

Version source of truth is ``src/kernel/kernel/__init__.py``. This script
updates all release-facing projections from that Kernel version and performs
the branch/tag operations used by the release workflow.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
KERNEL_VERSION_FILE = REPO_ROOT / "src/kernel/kernel/__init__.py"
CLI_PACKAGE_FILE = REPO_ROOT / "src/cli/package.json"
CLI_UTILS_FILE = REPO_ROOT / "src/cli/src/compat/utils.ts"
CLI_ACP_CLIENT_FILE = REPO_ROOT / "src/cli/src/acp/client.ts"
REMOTE = os.environ.get("DEEPCLI_RELEASE_REMOTE", "origin")

SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
PRERELEASE_RE = re.compile(r"^([0-9]+\.[0-9]+\.[0-9]+)(a|b|rc)[0-9]+$")

USAGE = """Usage:
  scripts/release.sh freeze [final-version]
  scripts/release.sh release
  scripts/release.sh tag
  scripts/release.sh fix

Windows:
  .\\scripts\\release.ps1 freeze [final-version]
  .\\scripts\\release.ps1 release
  .\\scripts\\release.ps1 tag
  .\\scripts\\release.ps1 fix

Commands:
  freeze  Run from dev. Shows the current Kernel version, asks for
                  the next final version if omitted, bumps to <version>a1,
                  commits, resets the fixed freeze branch to dev,
                  and pushes freeze.

  release         Run from freeze. Removes the prerelease suffix
                  from the current Kernel version, commits, pushes
                  freeze, fast-forwards main, tags v<version>,
                  and pushes main + tag.

  tag             Run from main. Checks the current Kernel version is a
                  final version, pushes main, tags v<version>, and pushes
                  the tag. This is useful for the first bootstrap release
                  or for tagging an already-final release commit.

  fix             Run from main. Retargets the existing v<version> tag to
                  the current HEAD after a failed bootstrap release CI.
                  This deletes and recreates the local + remote tag.

Environment:
  DEEPCLI_RELEASE_REMOTE  Git remote to push. Default: origin.
  DEEPCLI_RELEASE_YES=1   Skip confirmation prompts.
"""


def die(message: str) -> None:
    print(f"release.py: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(
    args: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    stderr: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=stderr,
    )


def git(*args: str, check: bool = True, capture: bool = False, stderr: int | None = None) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], check=check, capture=capture, stderr=stderr)


def confirm(prompt: str) -> None:
    if os.environ.get("DEEPCLI_RELEASE_YES") == "1":
        return
    answer = input(f"{prompt} [y/N] ")
    if answer not in {"y", "Y", "yes", "YES"}:
        die("aborted")


def current_branch() -> str:
    return git("branch", "--show-current", capture=True).stdout.strip()


def require_branch(expected: str) -> None:
    actual = current_branch()
    if actual != expected:
        die(f"expected branch '{expected}', got '{actual}'")


def require_clean_worktree() -> None:
    status = git("status", "--porcelain", capture=True).stdout
    if status:
        print(git("status", "--short", capture=True).stdout, file=sys.stderr, end="")
        die("worktree must be clean before release branch operations")


def read_kernel_version() -> str:
    text = KERNEL_VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', text, re.M)
    if not match:
        die("cannot find kernel __version__")
    return match.group(1)


def validate_final_version(version: str) -> None:
    if not SEMVER_RE.match(version):
        die(f"expected final semver like 1.2.3, got '{version}'")


def strip_prerelease_suffix(version: str) -> str:
    match = PRERELEASE_RE.match(version)
    if not match:
        die(
            "expected prerelease version like 1.2.3a1, 1.2.3b1, "
            f"or 1.2.3rc1; got '{version}'"
        )
    return match.group(1)


def replace_one(path: Path, pattern: str, replacement: str, error: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.M)
    if count != 1:
        die(error)
    path.write_text(new_text, encoding="utf-8")


def replace_versions(version: str) -> None:
    replace_one(
        KERNEL_VERSION_FILE,
        r'^__version__ = "([^"]+)"$',
        f'__version__ = "{version}"',
        "kernel version replacement failed",
    )

    package_data = json.loads(CLI_PACKAGE_FILE.read_text(encoding="utf-8"))
    package_data["version"] = version
    CLI_PACKAGE_FILE.write_text(json.dumps(package_data, indent="\t") + "\n", encoding="utf-8")

    replace_one(
        CLI_UTILS_FILE,
        r'export const VERSION = "([^"]+)";',
        f'export const VERSION = "{version}";',
        "CLI fallback version replacement failed",
    )

    replace_one(
        CLI_ACP_CLIENT_FILE,
        r'clientInfo: \{ name: "deepcli-cli", version: "([^"]+)" \}',
        f'clientInfo: {{ name: "deepcli-cli", version: "{version}" }}',
        "ACP client version replacement failed",
    )


def check_versions() -> None:
    expected = read_kernel_version()
    errors: list[str] = []

    package_version = json.loads(CLI_PACKAGE_FILE.read_text(encoding="utf-8")).get("version")
    if package_version != expected:
        errors.append(f"package.json version {package_version!r} != Kernel {expected!r}")

    utils_text = CLI_UTILS_FILE.read_text(encoding="utf-8")
    utils_match = re.search(r'export const VERSION = "([^"]+)";', utils_text)
    utils_version = utils_match.group(1) if utils_match else None
    if utils_version != expected:
        errors.append(f"CLI fallback VERSION {utils_version!r} != Kernel {expected!r}")

    acp_text = CLI_ACP_CLIENT_FILE.read_text(encoding="utf-8")
    acp_match = re.search(r'clientInfo: \{ name: "deepcli-cli", version: "([^"]+)" \}', acp_text)
    acp_version = acp_match.group(1) if acp_match else None
    if acp_version != expected:
        errors.append(f"ACP clientInfo.version {acp_version!r} != Kernel {expected!r}")

    if errors:
        die("\n".join(errors))
    print(f"version-ok {expected}")


def commit_version_bump(version: str, message: str) -> None:
    git(
        "add",
        str(KERNEL_VERSION_FILE),
        str(CLI_PACKAGE_FILE),
        str(CLI_UTILS_FILE),
        str(CLI_ACP_CLIENT_FILE),
    )
    git("commit", "-m", message)
    print(f"Committed version {version}")


def cmd_freeze(args: list[str]) -> None:
    if len(args) > 1:
        die("freeze accepts at most one final-version argument")
    require_branch("dev")
    require_clean_worktree()

    previous = read_kernel_version()
    print(f"Current Kernel version: {previous}")

    next_final = args[0] if args else input("Freeze final version (for example 1.1.0): ")
    validate_final_version(next_final)
    next_freeze = f"{next_final}a1"

    print(f"Freeze version will be: {next_freeze}")
    confirm(f"Reset fixed branch 'freeze' from dev and push {next_freeze} to {REMOTE}?")

    git("fetch", REMOTE, "dev", "freeze", "main", "--prune", check=False)
    git("checkout", "-B", "freeze", "dev")
    replace_versions(next_freeze)
    check_versions()
    commit_version_bump(next_freeze, f"release: start freeze {next_freeze}")
    git("push", "--force-with-lease", REMOTE, "freeze")


def cmd_release(args: list[str]) -> None:
    if args:
        die("release does not accept arguments")
    require_branch("freeze")
    require_clean_worktree()

    current = read_kernel_version()
    final = strip_prerelease_suffix(current)

    print(f"Current freeze version: {current}")
    print(f"Final release version will be: {final}")
    confirm(f"Promote freeze to main, tag v{final}, and push to {REMOTE}?")

    git("fetch", REMOTE, "main", "freeze", "--prune", check=False)
    replace_versions(final)
    check_versions()
    commit_version_bump(final, f"release: v{final}")
    git("push", REMOTE, "freeze")

    git("checkout", "main")
    git("pull", "--ff-only", REMOTE, "main")
    git("merge", "--ff-only", "freeze")
    git("push", REMOTE, "main")
    git("tag", f"v{final}")
    git("push", REMOTE, f"v{final}")


def cmd_tag_current(args: list[str]) -> None:
    if args:
        die("tag does not accept arguments")
    require_branch("main")
    require_clean_worktree()

    current = read_kernel_version()
    validate_final_version(current)
    check_versions()

    if git("rev-parse", "-q", "--verify", f"refs/tags/v{current}", check=False).returncode == 0:
        die(f"tag v{current} already exists locally")
    if git(
        "ls-remote",
        "--exit-code",
        "--tags",
        REMOTE,
        f"refs/tags/v{current}",
        check=False,
        stderr=subprocess.DEVNULL,
    ).returncode == 0:
        die(f"tag v{current} already exists on {REMOTE}")

    print(f"Current final version: {current}")
    confirm(f"Push main, tag v{current}, and push the tag to {REMOTE}?")

    git("push", REMOTE, "main")
    git("tag", f"v{current}")
    git("push", REMOTE, f"v{current}")


def cmd_fix_tag(args: list[str]) -> None:
    if args:
        die("fix does not accept arguments")
    require_branch("main")
    require_clean_worktree()

    current = read_kernel_version()
    validate_final_version(current)
    check_versions()
    head = git("rev-parse", "HEAD", capture=True).stdout.strip()

    remote_tag_result = git(
        "ls-remote",
        "--exit-code",
        "--tags",
        REMOTE,
        f"refs/tags/v{current}",
        check=False,
        capture=True,
        stderr=subprocess.DEVNULL,
    )
    if remote_tag_result.returncode != 0:
        die(f"remote tag v{current} does not exist on {REMOTE}; use 'scripts/release.sh tag' instead")

    print(f"Current final version: {current}")
    print(f"Current HEAD: {head}")
    print(f"Existing remote tag: {remote_tag_result.stdout.strip()}")
    confirm(f"Move v{current} to current HEAD, deleting and recreating the local + remote tag on {REMOTE}?")

    git("push", REMOTE, "main")
    git("tag", "-d", f"v{current}", check=False, capture=True, stderr=subprocess.DEVNULL)
    git("push", REMOTE, f":refs/tags/v{current}")
    git("tag", f"v{current}")
    git("push", REMOTE, f"v{current}")


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    args = argv[1:]

    if command in {"", "--help", "-h", "help"}:
        print(USAGE, end="")
        return 0
    if command == "freeze":
        cmd_freeze(args)
    elif command == "release":
        cmd_release(args)
    elif command == "tag":
        cmd_tag_current(args)
    elif command == "fix":
        cmd_fix_tag(args)
    elif command == "check-version":
        if args:
            die("check-version does not accept arguments")
        check_versions()
    elif command == "read-version":
        if args:
            die("read-version does not accept arguments")
        print(read_kernel_version())
    else:
        print(USAGE, file=sys.stderr, end="")
        die(f"unknown command: {command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
