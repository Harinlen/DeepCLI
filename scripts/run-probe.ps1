# Start deepcli-probe (interactive ACP test client).

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Push-Location (Join-Path $RepoRoot "src/probe")
try {
    & uv run python -m probe @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
