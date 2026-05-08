#!/usr/bin/env bash
# DeepCLI release branch helper.
#
# Usage:
#   scripts/release.sh freeze [final-version]
#   scripts/release.sh release
#   scripts/release.sh tag
#   scripts/release.sh fix
#
# Version source of truth is src/kernel/kernel/__init__.py.  This script
# updates all release-facing projections from that Kernel version.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
kernel_version_file="$repo_root/src/kernel/kernel/__init__.py"
cli_package_file="$repo_root/src/cli/package.json"
cli_utils_file="$repo_root/src/cli/src/compat/utils.ts"
cli_acp_client_file="$repo_root/src/cli/src/acp/client.ts"
remote="${DEEPCLI_RELEASE_REMOTE:-origin}"

usage() {
  cat <<'EOF'
Usage:
  scripts/release.sh freeze [final-version]
  scripts/release.sh release
  scripts/release.sh tag
  scripts/release.sh fix

Commands:
  freeze  Run from dev.  Shows the current Kernel version, asks for
                  the next final version if omitted, bumps to <version>a1,
                  commits, resets the fixed freeze branch to dev,
                  and pushes freeze.

  release         Run from freeze.  Removes the prerelease suffix
                  from the current Kernel version, commits, pushes
                  freeze, fast-forwards main, tags v<version>,
                  and pushes main + tag.

  tag             Run from main.  Checks the current Kernel version is a
                  final version, pushes main, tags v<version>, and pushes
                  the tag.  This is useful for the first bootstrap release
                  or for tagging an already-final release commit.

  fix             Run from main.  Retargets the existing v<version> tag to
                  the current HEAD after a failed bootstrap release CI.
                  This deletes and recreates the local + remote tag.

Environment:
  DEEPCLI_RELEASE_REMOTE  Git remote to push. Default: origin.
  DEEPCLI_RELEASE_YES=1   Skip confirmation prompts.
EOF
}

die() {
  echo "release.sh: $*" >&2
  exit 1
}

confirm() {
  if [ "${DEEPCLI_RELEASE_YES:-}" = "1" ]; then
    return 0
  fi
  local prompt="$1"
  local answer
  read -r -p "$prompt [y/N] " answer
  case "$answer" in
    y|Y|yes|YES ) ;;
    * ) die "aborted" ;;
  esac
}

current_branch() {
  git -C "$repo_root" branch --show-current
}

require_branch() {
  local expected="$1"
  local actual
  actual="$(current_branch)"
  [ "$actual" = "$expected" ] || die "expected branch '$expected', got '$actual'"
}

require_clean_worktree() {
  if [ -n "$(git -C "$repo_root" status --porcelain)" ]; then
    git -C "$repo_root" status --short >&2
    die "worktree must be clean before release branch operations"
  fi
}

read_kernel_version() {
  python3 - "$kernel_version_file" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text()
match = re.search(r'^__version__ = "([^"]+)"$', text, re.M)
if not match:
    raise SystemExit("cannot find kernel __version__")
print(match.group(1))
PY
}

semver_re='^[0-9]+\.[0-9]+\.[0-9]+$'
prerelease_re='^([0-9]+\.[0-9]+\.[0-9]+)(a|b|rc)[0-9]+$'

validate_final_version() {
  local version="$1"
  [[ "$version" =~ $semver_re ]] || die "expected final semver like 1.2.3, got '$version'"
}

strip_prerelease_suffix() {
  local version="$1"
  if [[ "$version" =~ $prerelease_re ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  die "expected prerelease version like 1.2.3a1, 1.2.3b1, or 1.2.3rc1; got '$version'"
}

replace_versions() {
  local version="$1"
  python3 - "$version" "$kernel_version_file" "$cli_package_file" "$cli_utils_file" "$cli_acp_client_file" <<'PY'
import json
import re
import sys
from pathlib import Path

version, kernel_path, package_path, utils_path, acp_path = sys.argv[1:]

kernel = Path(kernel_path)
kernel_text = kernel.read_text()
kernel_text_new = re.sub(
    r'^__version__ = "([^"]+)"$',
    f'__version__ = "{version}"',
    kernel_text,
    count=1,
    flags=re.M,
)
if kernel_text_new == kernel_text:
    raise SystemExit("kernel version replacement failed")
kernel.write_text(kernel_text_new)

package = Path(package_path)
package_data = json.loads(package.read_text())
package_data["version"] = version
package.write_text(json.dumps(package_data, indent="\t") + "\n")

utils = Path(utils_path)
utils_text = utils.read_text()
utils_text_new = re.sub(
    r'export const VERSION = "([^"]+)";',
    f'export const VERSION = "{version}";',
    utils_text,
    count=1,
)
if utils_text_new == utils_text:
    raise SystemExit("CLI fallback version replacement failed")
utils.write_text(utils_text_new)

acp = Path(acp_path)
acp_text = acp.read_text()
acp_text_new = re.sub(
    r'clientInfo: \{ name: "deepcli-cli", version: "([^"]+)" \}',
    f'clientInfo: {{ name: "deepcli-cli", version: "{version}" }}',
    acp_text,
    count=1,
)
if acp_text_new == acp_text:
    raise SystemExit("ACP client version replacement failed")
acp.write_text(acp_text_new)
PY
}

check_versions() {
  local expected
  expected="$(read_kernel_version)"
  python3 - "$expected" "$cli_package_file" "$cli_utils_file" "$cli_acp_client_file" <<'PY'
import json
import re
import sys
from pathlib import Path

expected, package_path, utils_path, acp_path = sys.argv[1:]
errors: list[str] = []

package_version = json.loads(Path(package_path).read_text()).get("version")
if package_version != expected:
    errors.append(f"package.json version {package_version!r} != Kernel {expected!r}")

utils_text = Path(utils_path).read_text()
utils_match = re.search(r'export const VERSION = "([^"]+)";', utils_text)
utils_version = utils_match.group(1) if utils_match else None
if utils_version != expected:
    errors.append(f"CLI fallback VERSION {utils_version!r} != Kernel {expected!r}")

acp_text = Path(acp_path).read_text()
acp_match = re.search(r'clientInfo: \{ name: "deepcli-cli", version: "([^"]+)" \}', acp_text)
acp_version = acp_match.group(1) if acp_match else None
if acp_version != expected:
    errors.append(f"ACP clientInfo.version {acp_version!r} != Kernel {expected!r}")

if errors:
    raise SystemExit("\n".join(errors))
print(f"version-ok {expected}")
PY
}

commit_version_bump() {
  local version="$1"
  local message="$2"
  git -C "$repo_root" add \
    "$kernel_version_file" \
    "$cli_package_file" \
    "$cli_utils_file" \
    "$cli_acp_client_file"
  git -C "$repo_root" commit -m "$message"
  echo "Committed version $version"
}

cmd_freeze() {
  require_branch dev
  require_clean_worktree

  local previous next_final next_freeze
  previous="$(read_kernel_version)"
  echo "Current Kernel version: $previous"

  next_final="${1:-}"
  if [ -z "$next_final" ]; then
    read -r -p "Freeze final version (for example 1.1.0): " next_final
  fi
  validate_final_version "$next_final"
  next_freeze="${next_final}a1"

  echo "Freeze version will be: $next_freeze"
  confirm "Reset fixed branch 'freeze' from dev and push $next_freeze to $remote?"

  git -C "$repo_root" fetch "$remote" dev freeze main --prune || true
  git -C "$repo_root" checkout -B freeze dev
  replace_versions "$next_freeze"
  check_versions
  commit_version_bump "$next_freeze" "release: start freeze $next_freeze"
  git -C "$repo_root" push --force-with-lease "$remote" freeze
}

cmd_release() {
  require_branch freeze
  require_clean_worktree

  local current final
  current="$(read_kernel_version)"
  final="$(strip_prerelease_suffix "$current")"

  echo "Current freeze version: $current"
  echo "Final release version will be: $final"
  confirm "Promote freeze to main, tag v$final, and push to $remote?"

  git -C "$repo_root" fetch "$remote" main freeze --prune || true
  replace_versions "$final"
  check_versions
  commit_version_bump "$final" "release: v$final"
  git -C "$repo_root" push "$remote" freeze

  git -C "$repo_root" checkout main
  git -C "$repo_root" pull --ff-only "$remote" main
  git -C "$repo_root" merge --ff-only freeze
  git -C "$repo_root" push "$remote" main
  git -C "$repo_root" tag "v$final"
  git -C "$repo_root" push "$remote" "v$final"
}

cmd_tag_current() {
  require_branch main
  require_clean_worktree

  local current
  current="$(read_kernel_version)"
  validate_final_version "$current"
  check_versions

  if git -C "$repo_root" rev-parse -q --verify "refs/tags/v$current" >/dev/null; then
    die "tag v$current already exists locally"
  fi
  if git -C "$repo_root" ls-remote --exit-code --tags "$remote" "refs/tags/v$current" >/dev/null 2>&1; then
    die "tag v$current already exists on $remote"
  fi

  echo "Current final version: $current"
  confirm "Push main, tag v$current, and push the tag to $remote?"

  git -C "$repo_root" push "$remote" main
  git -C "$repo_root" tag "v$current"
  git -C "$repo_root" push "$remote" "v$current"
}

cmd_fix_tag() {
  require_branch main
  require_clean_worktree

  local current head remote_tag
  current="$(read_kernel_version)"
  validate_final_version "$current"
  check_versions
  head="$(git -C "$repo_root" rev-parse HEAD)"

  if ! remote_tag="$(git -C "$repo_root" ls-remote --exit-code --tags "$remote" "refs/tags/v$current" 2>/dev/null)"; then
    die "remote tag v$current does not exist on $remote; use 'scripts/release.sh tag' instead"
  fi

  echo "Current final version: $current"
  echo "Current HEAD: $head"
  echo "Existing remote tag: $remote_tag"
  confirm "Move v$current to current HEAD, deleting and recreating the local + remote tag on $remote?"

  git -C "$repo_root" push "$remote" main
  git -C "$repo_root" tag -d "v$current" >/dev/null 2>&1 || true
  git -C "$repo_root" push "$remote" ":refs/tags/v$current"
  git -C "$repo_root" tag "v$current"
  git -C "$repo_root" push "$remote" "v$current"
}

case "${1:-}" in
  freeze )
    shift
    cmd_freeze "$@"
    ;;
  release )
    shift
    [ "$#" -eq 0 ] || die "release does not accept arguments"
    cmd_release
    ;;
  tag )
    shift
    [ "$#" -eq 0 ] || die "tag does not accept arguments"
    cmd_tag_current
    ;;
  fix )
    shift
    [ "$#" -eq 0 ] || die "fix does not accept arguments"
    cmd_fix_tag
    ;;
  check-version )
    check_versions
    ;;
  read-version )
    read_kernel_version
    ;;
  --help|-h|help|"" )
    usage
    ;;
  * )
    usage >&2
    die "unknown command: $1"
    ;;
esac
