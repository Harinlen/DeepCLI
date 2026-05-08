# Setup

This page is for source development. For normal Linux x86_64 or macOS use,
install the latest release instead:

```bash
sh -c "$(curl -fsSL https://github.com/Harinlen/DeepCLI/releases/latest/download/install.sh)"
```

macOS:

```bash
sh -c "$(curl -fsSL https://github.com/Harinlen/DeepCLI/releases/latest/download/install-macos.sh)"
```

The release installer does not require Node, npm, Bun, uv, or a pre-existing
virtualenv on the user machine. It installs user-local artifacts and a
DeepCLI-private `uv` + managed Python runtime.

The rest of this page covers source checkouts on Linux / macOS / Windows.

## Prerequisites

| Tool | Min version | Linux | macOS | Windows |
|---|---|---|---|---|
| Python | 3.12+ | `sudo apt install python3.12` | `brew install python@3.12` | [python.org](https://www.python.org/downloads/) |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | same | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| ripgrep | any | `sudo apt install ripgrep` | `brew install ripgrep` | `scoop install ripgrep` |
| cloc | any | `sudo apt install cloc` | `brew install cloc` | `scoop install cloc` |
| git | any | built-in | built-in | [git-scm.com](https://git-scm.com/) |

## Source Checkout

For agent-driven setup, see [`INIT.md`](../INIT.md) — it does a
preflight check first and only runs the steps that are actually
missing, so it's safe to re-run on an already-configured machine.

Manual steps (first-time only):

```bash
git clone <repo-url> deepcli && cd deepcli
uv sync
uv run pytest -q tests/
cp .mustang-refs.example.yaml .mustang-refs.yaml  # fill in local paths
```

The default pytest command is the non-E2E environment smoke check.
Live-kernel E2E tests are marked `e2e` and are run explicitly when a
feature or closure seam needs real-system verification.

## Checking whether this machine is already set up

Idempotent checks — safe to run anytime:

```bash
uv --version                      # uv installed?
uv sync --locked --check          # deps in sync with lockfile?
test -f .mustang-refs.yaml        # per-machine ref paths configured?
./resolve-ref.sh claude-code
./resolve-ref.sh openclaw
./resolve-ref.sh hermes-agent  # all three refs resolvable?
```

If all five pass, the dev environment is ready — no need to re-run
`uv sync` or recreate `.mustang-refs.yaml`.

## Local LLM

DeepCLI model configuration lives in:

```text
~/.deepcli/config/kernel.yaml
```

Point it at whatever you run locally or at a hosted provider:

```yaml
llm:
  current_used:
    default:
      provider: local
      model: qwen3.5
  providers:
    local:
      type: openai_compatible
      base_url: http://127.0.0.1:8080/v1
      api_key: local
      models:
        - id: qwen3.5
```

Current dev target: **llama.cpp + Qwen3.5** (function calling
supported). No Anthropic API key required.

## Running From Source

### Start the dev Supervisor

First stop any existing dev kernel process, then launch:

```bash
# Kill existing kernel if running
lsof -ti:8200 | xargs -r kill

# Start the dev Supervisor, Access Agent, Agent Hub, and Primary Agent.
scripts/run-kernel.sh
```

The dev Supervisor exposes Access Agent on `http://127.0.0.1:8200`.

In another terminal, run the CLI from source:

```bash
scripts/run-cli.sh
```

### Run Tests

Default smoke/unit tests (no running kernel needed; E2E excluded by
the default `not e2e` marker filter):

```bash
uv run pytest -q tests/
```

E2E tests (spawns a temporary kernel on port 18200):

```bash
uv run pytest -m e2e tests/e2e/
```

## Quality Toolchain

```bash
uv run ruff format src/
uv run ruff check src/
uv run mypy src/
uv run pytest --cov=src tests/
cloc src/ --by-percent c         # comment density target 20–25%
uv run bandit -r src/ -q
```

## Deployment Targets

Linux (x86_64, ARM64 incl. Raspberry Pi 4/5 with 4 GB+ RAM),
macOS (x86_64, Apple Silicon), Windows (x86_64).

When adding a compiled dependency, check wheel availability on armv7
(32-bit Pi).  Missing armv7 wheels are acceptable only if the
feature has a significant win and can be conditionally disabled on
32-bit targets (see `docs/reference/decisions.md` D-deferred-1
for an applied example).

## IDE (VS Code)

Recommended extensions: Python, Ruff, mypy Type Checker.

```json
{
  "python.defaultInterpreterPath": ".venv/bin/python",
  "ruff.enable": true,
  "mypy-type-checker.args": ["--strict"]
}
```

Windows: `.venv\\Scripts\\python.exe`.
