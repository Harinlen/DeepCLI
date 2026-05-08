$ErrorActionPreference = "Stop"

$RequestedVersion = if ($env:DEEPCLI_VERSION) { $env:DEEPCLI_VERSION } else { "latest" }
$LocalDir = $env:DEEPCLI_LOCAL_DIR
$HomeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { [Environment]::GetFolderPath("UserProfile") }
$InstallDir = if ($env:DEEPCLI_INSTALL_DIR) { $env:DEEPCLI_INSTALL_DIR } else { Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "DeepCLI" }
$DeepCliHome = if ($env:DEEPCLI_HOME) { $env:DEEPCLI_HOME } else { Join-Path $HomeDir ".deepcli" }
$StateDir = if ($env:DEEPCLI_STATE_DIR) { $env:DEEPCLI_STATE_DIR } else { Join-Path $DeepCliHome "state" }
$ConfigDir = if ($env:DEEPCLI_CONFIG_DIR) { $env:DEEPCLI_CONFIG_DIR } else { $DeepCliHome }
$RuntimeDir = Join-Path $StateDir "runtime"
$RuntimeStateFile = Join-Path $RuntimeDir "launcher-runtime.json"
$LauncherLockFile = Join-Path $StateDir "launcher.lock"
$BinDir = Join-Path $InstallDir "bin"
$ToolsDir = Join-Path $InstallDir "tools"
$DownloadsDir = Join-Path $InstallDir "downloads"
$UvVersion = if ($env:DEEPCLI_UV_VERSION) { $env:DEEPCLI_UV_VERSION } else { "0.9.28" }
$PythonVersion = if ($env:DEEPCLI_PYTHON_VERSION) { $env:DEEPCLI_PYTHON_VERSION } else { "3.13" }
$UvBin = Join-Path $ToolsDir "uv\$UvVersion\uv.exe"
$PythonInstallDir = Join-Path $ToolsDir "python"
$ZipName = "deepcli-windows-amd64.zip"
$RestartKernelAfterInstall = $false

New-Item -ItemType Directory -Force -Path $BinDir, (Join-Path $InstallDir "releases"), $ToolsDir, $DownloadsDir, $RuntimeDir, $ConfigDir | Out-Null

function Invoke-WithInstallLock([scriptblock] $Body) {
    $stream = [IO.File]::Open($LauncherLockFile, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        & $Body
    }
    finally {
        $stream.Dispose()
    }
}

function Get-ReadinessUrl([int] $Port) {
    return "http://127.0.0.1:$Port/access/readiness"
}

function Test-Ready([int] $Port) {
    try {
        $payload = Invoke-RestMethod -Uri (Get-ReadinessUrl $Port) -TimeoutSec 1
        return (
            $payload.default_route_ready -eq $true -and
            $payload.hub_ready -eq $true -and
            $payload.primary_registered -eq $true
        )
    }
    catch {
        return $false
    }
}

function Read-RuntimeState {
    if (-not (Test-Path $RuntimeStateFile)) {
        return $null
    }
    try {
        return Get-Content -Raw -Path $RuntimeStateFile | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Stop-RunningKernelForUpgrade {
    if ($env:DEEPCLI_INSTALL_KEEP_KERNEL -eq "1") {
        return
    }
    $state = Read-RuntimeState
    if (-not $state -or -not $state.pid) {
        return
    }

    $wasRunning = $false
    if ($state.port -and (Test-Ready ([int] $state.port))) {
        $wasRunning = $true
    }
    elseif (Get-Process -Id ([int] $state.pid) -ErrorAction SilentlyContinue) {
        $wasRunning = $true
    }

    if (-not $wasRunning) {
        Remove-Item -Force -ErrorAction SilentlyContinue $RuntimeStateFile
        return
    }

    $script:RestartKernelAfterInstall = $true
    Write-Output "Stopping running DeepCLI Kernel before install..."
    $stopPid = [int] $state.pid
    if (Get-Command taskkill.exe -ErrorAction SilentlyContinue) {
        & cmd.exe /c "taskkill /PID $stopPid /T >NUL 2>NUL"
    }
    else {
        Stop-Process -Id $stopPid -ErrorAction SilentlyContinue
    }

    for ($i = 0; $i -lt 40; $i++) {
        $processAlive = Get-Process -Id ([int] $state.pid) -ErrorAction SilentlyContinue
        $ready = $state.port -and (Test-Ready ([int] $state.port))
        if (-not $processAlive -and -not $ready) {
            Remove-Item -Force -ErrorAction SilentlyContinue $RuntimeStateFile
            return
        }
        Start-Sleep -Milliseconds 250
    }

    if (Get-Command taskkill.exe -ErrorAction SilentlyContinue) {
        & cmd.exe /c "taskkill /PID $stopPid /T /F >NUL 2>NUL"
    }
    else {
        Stop-Process -Id ([int] $state.pid) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -Force -ErrorAction SilentlyContinue $RuntimeStateFile
}

function Restart-KernelIfNeeded {
    if (-not $script:RestartKernelAfterInstall) {
        return
    }
    Write-Output "Restarting DeepCLI Kernel with installed version..."
    & (Join-Path $BinDir "deepcli.cmd") kernel start
}

function Copy-LocalArtifact([string] $Name, [string] $Destination) {
    if (-not $LocalDir) {
        throw "Windows install.ps1 currently requires DEEPCLI_LOCAL_DIR. Use install-dev.ps1 for local installs."
    }
    $source = Join-Path $LocalDir $Name
    if (-not (Test-Path $source)) {
        throw "Local artifact not found: $source"
    }
    $tmp = "$Destination.tmp"
    Copy-Item -Force -Path $source -Destination $tmp
    Move-Item -Force -Path $tmp -Destination $Destination
}

function Verify-Checksums([string] $ArtifactPath, [string] $ChecksumPath) {
    $artifactFile = Split-Path -Leaf $ArtifactPath
    $line = Get-Content -Path $ChecksumPath | Where-Object { $_ -match "\s\s$([Regex]::Escape($artifactFile))$" } | Select-Object -First 1
    if (-not $line) {
        throw "No checksum found for $artifactFile"
    }
    $expected = ($line -split "\s+")[0].ToLowerInvariant()
    $actual = (Get-FileHash -Algorithm SHA256 -Path $ArtifactPath).Hash.ToLowerInvariant()
    if ($expected -ne $actual) {
        throw "Checksum mismatch for $artifactFile"
    }
}

function Install-PrivateUv {
    if (Test-Path $UvBin) {
        return
    }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $UvBin) | Out-Null

    if ($env:DEEPCLI_LOCAL_UV) {
        Copy-Item -Force -Path $env:DEEPCLI_LOCAL_UV -Destination $UvBin
        return
    }

    $pathUv = Get-Command uv -ErrorAction SilentlyContinue
    if ($pathUv) {
        Copy-Item -Force -Path $pathUv.Source -Destination $UvBin
        return
    }

    $privateUvArchive = "uv-x86_64-pc-windows-msvc.zip"
    $privateUvUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/$privateUvArchive"
    $privateUvTmpDir = Join-Path $DownloadsDir "uv-$UvVersion-$PID"
    New-Item -ItemType Directory -Force -Path $privateUvTmpDir | Out-Null

    Write-Output "Downloading private uv $UvVersion..."
    $archivePath = Join-Path $privateUvTmpDir $privateUvArchive
    Invoke-WebRequest -Uri $privateUvUrl -OutFile $archivePath
    Expand-Archive -Force -Path $archivePath -DestinationPath $privateUvTmpDir
    $uvExe = Get-ChildItem -Recurse -Path $privateUvTmpDir -Filter uv.exe | Select-Object -First 1
    if (-not $uvExe) {
        throw "Downloaded uv archive did not contain uv.exe"
    }
    Copy-Item -Force -Path $uvExe.FullName -Destination $UvBin
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $privateUvTmpDir
}

function Prepare-KernelVenv([string] $ReleasePath) {
    Write-Output "Preparing managed Python $PythonVersion..."
    $env:UV_PYTHON_INSTALL_DIR = $PythonInstallDir
    $env:UV_CACHE_DIR = Join-Path $InstallDir "cache\uv"
    & $UvBin python install $PythonVersion --managed-python --install-dir $PythonInstallDir --no-bin
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Write-Output "Preparing Kernel venv..."
    Push-Location (Join-Path $ReleasePath "kernel")
    try {
        & $UvBin sync --locked --no-dev --python $PythonVersion --managed-python
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    finally {
        Pop-Location
        Remove-Item Env:\UV_PYTHON_INSTALL_DIR -ErrorAction SilentlyContinue
        Remove-Item Env:\UV_CACHE_DIR -ErrorAction SilentlyContinue
    }
}

function Add-BinDirToUserPath {
    $separator = [IO.Path]::PathSeparator
    $currentProcessParts = @($env:PATH -split [Regex]::Escape($separator) | Where-Object { $_ })
    if ($currentProcessParts -notcontains $BinDir) {
        $env:PATH = $BinDir + $separator + $env:PATH
    }

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $userParts = @($userPath -split [Regex]::Escape($separator) | Where-Object { $_ })
    if ($userParts -contains $BinDir) {
        return
    }

    $newUserPath = if ($userPath) { $userPath.TrimEnd($separator) + $separator + $BinDir } else { $BinDir }
    [Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")

    Write-Output ""
    Write-Output "Added DeepCLI to your user PATH:"
    Write-Output "  $BinDir"
    Write-Output "Open a new terminal tab if this shell still cannot find 'deepcli'."
}

Invoke-WithInstallLock {
    Stop-RunningKernelForUpgrade

    Copy-LocalArtifact $ZipName (Join-Path $DownloadsDir $ZipName)
    Copy-LocalArtifact "checksums.txt" (Join-Path $DownloadsDir "checksums.txt")
    Verify-Checksums (Join-Path $DownloadsDir $ZipName) (Join-Path $DownloadsDir "checksums.txt")

    $tmpExtract = Join-Path $DownloadsDir "extract-$PID"
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmpExtract
    New-Item -ItemType Directory -Force -Path $tmpExtract | Out-Null
    Expand-Archive -Force -Path (Join-Path $DownloadsDir $ZipName) -DestinationPath $tmpExtract
    $releaseRoot = Get-ChildItem -Directory -Path $tmpExtract | Select-Object -First 1
    if (-not $releaseRoot -or -not (Test-Path (Join-Path $releaseRoot.FullName "VERSION"))) {
        throw "Release zip did not contain a valid DeepCLI release root."
    }
    $version = (Get-Content -Raw -Path (Join-Path $releaseRoot.FullName "VERSION")).Trim()
    $releaseDir = Join-Path $InstallDir "releases\$version"
    $releaseTmp = Join-Path $InstallDir "releases\.$version.tmp.$PID"

    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $releaseTmp
    Move-Item -Path $releaseRoot.FullName -Destination $releaseTmp
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmpExtract

    Install-PrivateUv
    Prepare-KernelVenv $releaseTmp

    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $releaseDir
    Move-Item -Path $releaseTmp -Destination $releaseDir

    $binPs1 = Join-Path $BinDir "deepcli.ps1"
    $binCmd = Join-Path $BinDir "deepcli.cmd"
    @"
`$ErrorActionPreference = "Stop"
`$env:DEEPCLI_RELEASE_DIR = "$releaseDir"
& "$releaseDir\launcher\deepcli.ps1" @args
exit `$LASTEXITCODE
"@ | Set-Content -Encoding UTF8 -Path $binPs1
    @"
@echo off
setlocal EnableExtensions
set "PS_EXE=pwsh"
where pwsh >nul 2>nul
if errorlevel 1 set "PS_EXE=powershell.exe"
set "DEEPCLI_RELEASE_DIR=$releaseDir"
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0deepcli.ps1" %*
exit /b %ERRORLEVEL%
"@ | Set-Content -Encoding ASCII -Path $binCmd

    Add-BinDirToUserPath

    Write-Output "DeepCLI installed: $(Join-Path $BinDir "deepcli.cmd")"
    & (Join-Path $BinDir "deepcli.cmd") --help | Out-Null
}

Restart-KernelIfNeeded
