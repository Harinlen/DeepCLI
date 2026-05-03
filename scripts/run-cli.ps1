# Start DeepCLI CLI (ACP terminal client), ensuring the dev kernel is ready.

$ErrorActionPreference = "Stop"

function Get-HostPowerShell {
    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwsh) {
        return $pwsh.Source
    }

    $powershell = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if ($powershell) {
        return $powershell.Source
    }

    throw "PowerShell executable not found."
}

function Quote-PowerShellLiteral([string] $Value) {
    return "'" + ($Value -replace "'", "''") + "'"
}

function Test-KernelReady([string] $Url) {
    try {
        $response = Invoke-RestMethod -Uri $Url -TimeoutSec 1
        return $response.default_route_ready -eq $true
    }
    catch {
        return $false
    }
}

function Get-BunCommand {
    if ($env:BUN) {
        return $env:BUN
    }

    $bun = Get-Command bun -ErrorAction SilentlyContinue
    if ($bun) {
        return $bun.Source
    }

    $homeBun = Join-Path $HOME ".bun/bin/bun.exe"
    if (Test-Path $homeBun) {
        return $homeBun
    }

    throw "Bun executable not found. Install Bun or set BUN to the executable path."
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$OriginalArgs = @($args)
$KernelPort = if ($env:KERNEL_PORT) { $env:KERNEL_PORT } else { "8200" }
$ExplicitKernelUrl = $env:KERNEL_URL
$BunExe = Get-BunCommand

for ($i = 0; $i -lt $OriginalArgs.Count; $i++) {
    $arg = $OriginalArgs[$i]
    switch ($arg) {
        { $_ -in @("--help", "-h", "help") } {
            Push-Location (Join-Path $RepoRoot "src/cli")
            try {
                & $BunExe run src/main.ts @OriginalArgs
                exit $LASTEXITCODE
            }
            finally {
                Pop-Location
            }
        }
        "--port" {
            if ($i + 1 -lt $OriginalArgs.Count) {
                $KernelPort = $OriginalArgs[$i + 1]
            }
        }
        "--kernel" {
            if ($i + 1 -lt $OriginalArgs.Count) {
                $ExplicitKernelUrl = $OriginalArgs[$i + 1]
            }
        }
    }
}

if (-not $ExplicitKernelUrl) {
    $ReadinessUrl = "http://127.0.0.1:$KernelPort/access/readiness"

    if (-not (Test-KernelReady $ReadinessUrl)) {
        $LogPath = Join-Path $RepoRoot "src/.run-kernel.log"
        $RunKernelScript = Join-Path $PSScriptRoot "run-kernel.ps1"
        $PowerShellExe = Get-HostPowerShell
        $Command = "& $(Quote-PowerShellLiteral $RunKernelScript) --access-port $(Quote-PowerShellLiteral $KernelPort) --dev *> $(Quote-PowerShellLiteral $LogPath)"
        $EncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Command))

        Start-Process `
            -FilePath $PowerShellExe `
            -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $EncodedCommand) `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden | Out-Null

        $Ready = $false
        for ($i = 0; $i -lt 160; $i++) {
            if (Test-KernelReady $ReadinessUrl) {
                $Ready = $true
                break
            }
            Start-Sleep -Milliseconds 250
        }

        if (-not $Ready) {
            Write-Error "Kernel did not become ready. See $LogPath"
            exit 1
        }
    }
}

Push-Location (Join-Path $RepoRoot "src/cli")
try {
    & $BunExe run src/main.ts @OriginalArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
