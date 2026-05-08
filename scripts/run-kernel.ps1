# Start Mustang through the Supervisor in dev mode.

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not $env:DEEPCLI_FLAGS_PATH -and -not $env:MUSTANG_FLAGS_PATH) {
    $DevFlagsPath = Join-Path $RepoRoot "src/.mustang-dev-flags.yaml"
    $DevFlags = @"
transport:
  stack: acp
"@
    [IO.File]::WriteAllText($DevFlagsPath, $DevFlags + "`n", [Text.UTF8Encoding]::new($false))
    $env:DEEPCLI_FLAGS_PATH = $DevFlagsPath
}

$SupervisorArgs = @($args)
if ($SupervisorArgs.Count -eq 0) {
    $SupervisorArgs = @("--access-port", "8200", "--dev")
}

Push-Location (Join-Path $RepoRoot "src/kernel")
try {
    & uv run python -m kernel.supervisor @SupervisorArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
