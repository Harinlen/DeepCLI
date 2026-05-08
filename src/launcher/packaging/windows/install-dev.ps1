# Build DeepCLI from this checkout and install it into the local user layout.

$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..\..\..")).Path
$Version = if ($env:DEEPCLI_VERSION) { $env:DEEPCLI_VERSION } else { "1.0.0" }
$ReleaseDir = if ($env:DEEPCLI_RELEASE_DIR) { $env:DEEPCLI_RELEASE_DIR } else { Join-Path $RepoRoot "dist\deepcli-windows-$Version" }

switch ($args[0]) {
    { $_ -in @("--help", "-h", "help") } {
        @"
Usage: install-dev.ps1

Build the current checkout into Windows release-shaped artifacts, then install
them into the local user layout through install.ps1.

Environment:
  DEEPCLI_VERSION       Version directory to install. Default: 1.0.0
  DEEPCLI_RELEASE_DIR   Local artifact output directory.
  DEEPCLI_INSTALL_KEEP_KERNEL=1
                        Do not stop/restart a running packaged Kernel.
"@ | Write-Output
        exit 0
    }
}

Write-Output "Building local DeepCLI release artifacts..."
$env:DEEPCLI_RELEASE_DIR = $ReleaseDir
& (Join-Path $ScriptDir "build-release.ps1")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Output ""
Write-Output "Installing from local artifacts..."
$env:DEEPCLI_LOCAL_DIR = $ReleaseDir
& (Join-Path $ScriptDir "install.ps1")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Output ""
Write-Output "Installed DeepCLI from checkout."
Write-Output "Release artifacts: $ReleaseDir"
Write-Output "Command: $([Environment]::GetFolderPath('LocalApplicationData'))\DeepCLI\bin\deepcli.cmd"
