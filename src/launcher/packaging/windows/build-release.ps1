$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$LauncherDir = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$RepoRoot = (Resolve-Path (Join-Path $LauncherDir "..\..")).Path
$Version = if ($env:DEEPCLI_VERSION) { $env:DEEPCLI_VERSION } else { "1.0.0" }
$OutDir = if ($env:DEEPCLI_RELEASE_DIR) { $env:DEEPCLI_RELEASE_DIR } else { Join-Path $RepoRoot "dist\deepcli-windows-$Version" }
$UvVersion = if ($env:DEEPCLI_UV_VERSION) { $env:DEEPCLI_UV_VERSION } else { "0.9.28" }
$PythonVersion = if ($env:DEEPCLI_PYTHON_VERSION) { $env:DEEPCLI_PYTHON_VERSION } else { "3.13" }

if (-not [IO.Path]::IsPathRooted($OutDir)) {
    $OutDir = Join-Path $RepoRoot $OutDir
}

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Windows v1 release builds support amd64 only."
}

$ReleaseName = "deepcli-$Version-windows-amd64"
$StageDir = Join-Path $OutDir "stage\$ReleaseName"
$ZipName = "deepcli-windows-amd64.zip"

function Get-BunCommand {
    if ($env:BUN) {
        return $env:BUN
    }
    $bun = Get-Command bun -ErrorAction SilentlyContinue
    if ($bun) {
        return $bun.Source
    }
    $homeBun = Join-Path $HOME ".bun\bin\bun.exe"
    if (Test-Path $homeBun) {
        return $homeBun
    }
    throw "Bun executable not found. Install Bun or set BUN to the executable path."
}

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $StageDir
New-Item -ItemType Directory -Force -Path `
    (Join-Path $StageDir "kernel"), `
    (Join-Path $StageDir "cli"), `
    (Join-Path $StageDir "launcher"), `
    (Join-Path $StageDir "assets"), `
    $OutDir | Out-Null

Write-Output "Staging Kernel source runtime..."
Copy-Item -Path (Join-Path $RepoRoot "src\kernel\pyproject.toml") -Destination (Join-Path $StageDir "kernel\pyproject.toml")
Copy-Item -Recurse -Path (Join-Path $RepoRoot "src\kernel\kernel") -Destination (Join-Path $StageDir "kernel\kernel")
Get-ChildItem -Path (Join-Path $StageDir "kernel\kernel") -Recurse -Force |
    Where-Object { $_.PSIsContainer -and $_.Name -in @("__pycache__", ".pytest_cache", ".mypy_cache") } |
    Remove-Item -Recurse -Force
Get-ChildItem -Path (Join-Path $StageDir "kernel\kernel") -Recurse -Force -Include *.pyc, *.pyo |
    Remove-Item -Force

Write-Output "Locking staged Kernel runtime dependencies..."
Push-Location (Join-Path $StageDir "kernel")
try {
    & uv lock --python $PythonVersion
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}

Write-Output "Staging Windows launcher..."
Copy-Item -Path (Join-Path $LauncherDir "bin\deepcli.ps1") -Destination (Join-Path $StageDir "launcher\deepcli.ps1")
Copy-Item -Path (Join-Path $LauncherDir "bin\deepcli.cmd") -Destination (Join-Path $StageDir "launcher\deepcli.cmd")

Write-Output "Staging default UI assets..."
Copy-Item -Path (Join-Path $RepoRoot "src\cli\src\active-port\coding-agent\modes\components\welcome-logo.txt") -Destination (Join-Path $StageDir "assets\welcome-logo.txt")

Write-Output "Building CLI single executable..."
$BunExe = Get-BunCommand
Push-Location (Join-Path $RepoRoot "src\cli")
try {
    & $BunExe build src/main.ts --target=bun --compile --outfile (Join-Path $StageDir "cli\deepcli-cli.exe")
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}

Set-Content -NoNewline -Encoding ASCII -Path (Join-Path $StageDir "VERSION") -Value $Version

Write-Output "Writing release zip..."
$ZipPath = Join-Path $OutDir $ZipName
Remove-Item -Force -ErrorAction SilentlyContinue $ZipPath
Compress-Archive -Path $StageDir -DestinationPath $ZipPath

Write-Output "Writing manifest..."
@"
{
  "version": "$Version",
  "arch": "amd64",
  "artifact": "$ZipName",
  "uvVersion": "$UvVersion",
  "pythonVersion": "$PythonVersion"
}
"@ | Set-Content -Encoding UTF8 -Path (Join-Path $OutDir "manifest.json")

Copy-Item -Path (Join-Path $ScriptDir "install.ps1") -Destination (Join-Path $OutDir "install.ps1")

Write-Output "Writing checksums..."
$ChecksumLines = foreach ($file in @($ZipName, "install.ps1", "manifest.json")) {
    $hash = (Get-FileHash -Algorithm SHA256 -Path (Join-Path $OutDir $file)).Hash.ToLowerInvariant()
    "$hash  $file"
}
$ChecksumLines | Set-Content -Encoding ASCII -Path (Join-Path $OutDir "checksums.txt")

Write-Output "Release artifacts written to $OutDir"
