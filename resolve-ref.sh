#!/usr/bin/env bash
# Look up a reference-project path from .mustang-refs.yaml.
#
# Usage: scripts/resolve-ref.sh <logical-name>
#
# Prints the absolute path on success (single line, no trailing
# newline garbage).  Exits with a non-zero status and a message on
# stderr if the name is unknown or the config file is missing.
#
# Agents: prefer this over reading the full YAML — it's one line of
# output per lookup, so token-cheap.

set -euo pipefail

CFG="$(git rev-parse --show-toplevel 2>/dev/null || pwd)/.mustang-refs.yaml"

if [[ ! -f "$CFG" ]]; then
    echo "error: $CFG not found — copy .mustang-refs.example.yaml and fill in local paths" >&2
    exit 2
fi

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <logical-name>" >&2
    exit 64
fi

NAME="$1"

# YAML format is deliberately simple (one key: value per line).
# Parse with awk; no yq dependency.
path=$(awk -F: -v name="$NAME" '
    /^[[:space:]]*#/ { next }
    $1 == name {
        # Rejoin everything after the first colon in case the path
        # itself contains a colon.
        sub(/^[^:]*:[[:space:]]*/, "")
        # Trim trailing whitespace.
        sub(/[[:space:]]+$/, "")
        print
        exit
    }
' "$CFG")

if [[ -z "$path" ]]; then
    echo "error: reference '$NAME' not in $CFG" >&2
    exit 1
fi

echo "$path"
