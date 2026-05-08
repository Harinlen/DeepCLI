# Build this checkout and install DeepCLI into the local Windows user layout.

$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot

switch ($args[0]) {
    { $_ -in @("--help", "-h", "help") } {
        @"
Usage: .\install-dev.ps1

Build this checkout into Windows release-shaped artifacts, then install them
into the local user layout through src\launcher\packaging\windows\install.ps1.

Environment:
  DEEPCLI_VERSION       Version directory to install. Default: 1.0.0
  DEEPCLI_RELEASE_DIR   Local artifact output directory.
  DEEPCLI_INSTALL_KEEP_KERNEL=1
                        Do not stop/restart a running packaged Kernel.
"@ | Write-Output
        exit 0
    }
}

& (Join-Path $RepoRoot "src\launcher\packaging\windows\install-dev.ps1") @args
exit $LASTEXITCODE
