# DeepCLI Windows launcher.

$ErrorActionPreference = "Stop"

$DefaultPort = if ($env:DEEPCLI_DEFAULT_PORT) { [int] $env:DEEPCLI_DEFAULT_PORT } else { 8200 }
$HostName = if ($env:DEEPCLI_HOST) { $env:DEEPCLI_HOST } else { "127.0.0.1" }
$HomeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { [Environment]::GetFolderPath("UserProfile") }
$DeepCliHome = if ($env:DEEPCLI_HOME) { $env:DEEPCLI_HOME } else { Join-Path $HomeDir ".deepcli" }
$StateDir = if ($env:DEEPCLI_STATE_DIR) { $env:DEEPCLI_STATE_DIR } else { Join-Path $DeepCliHome "state" }
$ConfigDir = if ($env:DEEPCLI_CONFIG_DIR) { $env:DEEPCLI_CONFIG_DIR } else { Join-Path $DeepCliHome "config" }
$DefaultInstallDir = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "DeepCLI"
$InstallDir = if ($env:DEEPCLI_INSTALL_DIR) { $env:DEEPCLI_INSTALL_DIR } else { $DefaultInstallDir }
$UvVersion = if ($env:DEEPCLI_UV_VERSION) { $env:DEEPCLI_UV_VERSION } else { "0.9.28" }
$PrivateUvBin = Join-Path $InstallDir "tools\uv\$UvVersion\uv.exe"
$RuntimeDir = Join-Path $StateDir "runtime"
$StateFile = Join-Path $RuntimeDir "launcher-runtime.json"
$LockFile = Join-Path $StateDir "launcher.lock"
$StdoutLog = Join-Path $RuntimeDir "supervisor.stdout.log"
$StderrLog = Join-Path $RuntimeDir "supervisor.stderr.log"
$InvocationCwd = (Get-Location).Path
$ScriptPath = $PSCommandPath
$ScriptDir = Split-Path -Parent $ScriptPath
$ReleaseDir = if ($env:DEEPCLI_RELEASE_DIR) { (Resolve-Path $env:DEEPCLI_RELEASE_DIR).Path } else { (Resolve-Path (Join-Path $ScriptDir "..")).Path }

New-Item -ItemType Directory -Force -Path $StateDir, $ConfigDir, $RuntimeDir | Out-Null

function Show-Usage {
    @"
Usage: deepcli [args...]
       deepcli status
       deepcli stop
       deepcli restart
       deepcli --uninstall
       deepcli kernel start [--port PORT]
       deepcli kernel logs

Default mode ensures the background Kernel Supervisor is ready, then starts
the foreground CLI and forwards all args to it.
"@ | Write-Output
}

function Test-DevRoot([string] $Root) {
    return (
        (Test-Path (Join-Path $Root "scripts\run-kernel.ps1")) -and
        (Test-Path (Join-Path $Root "src\cli\package.json")) -and
        (Test-Path (Join-Path $Root "src\kernel\pyproject.toml"))
    )
}

function Test-SourceLauncher {
    $maybeRoot = Resolve-Path (Join-Path $ScriptDir "..\..\..") -ErrorAction SilentlyContinue
    return $maybeRoot -and (Test-DevRoot $maybeRoot.Path)
}

function Find-DevRoot {
    if ($env:DEEPCLI_DEV_ROOT) {
        if (Test-DevRoot $env:DEEPCLI_DEV_ROOT) {
            return (Resolve-Path $env:DEEPCLI_DEV_ROOT).Path
        }
        throw "DEEPCLI_DEV_ROOT does not look like a DeepCLI checkout: $env:DEEPCLI_DEV_ROOT"
    }

    if (-not (Test-SourceLauncher)) {
        return $null
    }

    $dir = (Get-Location).Path
    while ($dir) {
        if (Test-DevRoot $dir) {
            return $dir
        }
        $parent = Split-Path -Parent $dir
        if ($parent -eq $dir) {
            break
        }
        $dir = $parent
    }
    return $null
}

function Get-BunCommand {
    if ($env:BUN) {
        return $env:BUN
    }
    $bun = Get-Command bun -ErrorAction SilentlyContinue
    if ($bun) {
        return $bun.Source
    }
    $homeBun = Join-Path $HomeDir ".bun\bin\bun.exe"
    if (Test-Path $homeBun) {
        return $homeBun
    }
    throw "Bun executable not found. Install Bun or set BUN to the executable path."
}

function Resolve-Layout {
    $devRoot = Find-DevRoot
    if ($devRoot) {
        return @{
            Mode = "dev"
            KernelCommand = @(Join-Path $devRoot "scripts\run-kernel.ps1")
            KernelCwd = Join-Path $devRoot "src"
            CliCommand = @((Get-BunCommand), "run", "src/main.ts")
            CliCwd = Join-Path $devRoot "src\cli"
        }
    }

    return @{
        Mode = "packaged"
        KernelCommand = @((Join-Path $ReleaseDir "kernel\.venv\Scripts\python.exe"), "-m", "kernel.supervisor")
        KernelCwd = Join-Path $ReleaseDir "kernel"
        CliCommand = @((Join-Path $ReleaseDir "cli\deepcli-cli.exe"))
        CliCwd = $InvocationCwd
    }
}

function Get-ReadinessUrl([int] $Port) {
    return "http://${HostName}:$Port/access/readiness"
}

function Get-WsUrl([int] $Port) {
    return "ws://${HostName}:$Port"
}

function Get-HealthUrl([int] $Port) {
    return "http://${HostName}:$Port/"
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

function Read-State {
    if (-not (Test-Path $StateFile)) {
        return $null
    }
    try {
        return Get-Content -Raw -Path $StateFile | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Write-State([int] $Port, [int] $ProcessId, [string] $Mode) {
    $state = [ordered] @{
        version = 1
        mode = $Mode
        port = $Port
        pid = $ProcessId
        startedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    $tmp = "$StateFile.tmp"
    $state | ConvertTo-Json | Set-Content -NoNewline -Encoding UTF8 -Path $tmp
    Move-Item -Force -Path $tmp -Destination $StateFile
}

function Test-PortFree([int] $Port) {
    $listener = $null
    try {
        $address = [Net.IPAddress]::Parse($HostName)
        $listener = [Net.Sockets.TcpListener]::new($address, $Port)
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($listener) {
            $listener.Stop()
        }
    }
}

function Get-FreePort {
    $address = [Net.IPAddress]::Parse($HostName)
    $listener = [Net.Sockets.TcpListener]::new($address, 0)
    try {
        $listener.Start()
        return $listener.LocalEndpoint.Port
    }
    finally {
        $listener.Stop()
    }
}

function Invoke-WithLauncherLock([scriptblock] $Body) {
    $stream = [IO.File]::Open($LockFile, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        & $Body
    }
    finally {
        $stream.Dispose()
    }
}

function Start-Supervisor([int] $Port, [hashtable] $Layout) {
    $command = @($Layout.KernelCommand)
    $exe = $command[0]
    if (-not (Test-Path $exe)) {
        throw "Kernel command was not found: $exe"
    }

    $arguments = @()
    if ($command.Count -gt 1) {
        $arguments += $command[1..($command.Count - 1)]
    }
    $arguments += @("--access-port", "$Port", "--state-dir", $RuntimeDir, "--workspace", $InvocationCwd)
    if ($Layout.Mode -eq "dev") {
        $arguments += "--dev"
    }

    $oldVirtualEnv = $env:VIRTUAL_ENV
    $oldPath = $env:PATH
    $oldDeepCliUvBin = $env:DEEPCLI_UV_BIN
    try {
        if ($Layout.Mode -eq "packaged") {
            $venv = Join-Path $ReleaseDir "kernel\.venv"
            $env:VIRTUAL_ENV = $venv
            $env:PATH = (Join-Path $venv "Scripts") + [IO.Path]::PathSeparator + $oldPath
            if (-not $env:DEEPCLI_UV_BIN -and (Test-Path $PrivateUvBin)) {
                $env:DEEPCLI_UV_BIN = $PrivateUvBin
            }
        }

        $process = Start-Process `
            -FilePath $exe `
            -ArgumentList $arguments `
            -WorkingDirectory $Layout.KernelCwd `
            -RedirectStandardOutput $StdoutLog `
            -RedirectStandardError $StderrLog `
            -WindowStyle Hidden `
            -PassThru
    }
    finally {
        $env:VIRTUAL_ENV = $oldVirtualEnv
        $env:PATH = $oldPath
        $env:DEEPCLI_UV_BIN = $oldDeepCliUvBin
    }

    Write-State -Port $Port -ProcessId $process.Id -Mode $Layout.Mode
}

function Wait-Ready([int] $Port) {
    for ($i = 0; $i -lt 160; $i++) {
        if (Test-Ready $Port) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Kernel Supervisor did not become ready. Logs: $StdoutLog ; $StderrLog"
}

function Ensure-Runtime([int] $RequestedPort) {
    $state = Read-State
    if ($state -and $state.port -and (Test-Ready ([int] $state.port))) {
        return [int] $state.port
    }

    Invoke-WithLauncherLock {
        $innerState = Read-State
        if ($innerState -and $innerState.port -and (Test-Ready ([int] $innerState.port))) {
            return
        }

        $port = $RequestedPort
        if (-not (Test-PortFree $port)) {
            if ($RequestedPort -ne $DefaultPort) {
                throw "Port $RequestedPort is already in use."
            }
            $port = Get-FreePort
        }

        $layout = Resolve-Layout
        Start-Supervisor -Port $port -Layout $layout
        Wait-Ready $port
    }

    $finalState = Read-State
    if (-not $finalState -or -not $finalState.port) {
        throw "Runtime state was not written."
    }
    return [int] $finalState.port
}

function Get-Token {
    if ($env:DEEPCLI_TOKEN) {
        return $env:DEEPCLI_TOKEN
    }
    if ($env:MUSTANG_TOKEN) {
        return $env:MUSTANG_TOKEN
    }
    $candidates = @(
        (Join-Path $RuntimeDir "auth_token"),
        (Join-Path $StateDir "auth_token"),
        (Join-Path $HomeDir ".mustang\state\auth_token")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            $token = (Get-Content -Raw -Path $candidate).Trim()
            if ($token) {
                return $token
            }
        }
    }
    throw "No DeepCLI auth token found. Checked runtime state and legacy dev token path."
}

function Show-Status {
    $state = Read-State
    if (-not $state -or -not $state.port) {
        Write-Output "runtime: not found"
        Write-Output "state: $StateDir"
        return
    }
    if (Test-Ready ([int] $state.port)) {
        Write-Output "runtime: ready"
    }
    else {
        Write-Output "runtime: not ready"
    }
    Write-Output "pid: $($state.pid)"
    Write-Output "port: $($state.port)"
    Write-Output "readiness: $(Get-ReadinessUrl ([int] $state.port))"
    Write-Output "state: $StateDir"
}

function Stop-Runtime {
    $state = Read-State
    if (-not $state -or -not $state.pid) {
        Write-Output "runtime: not running"
        return
    }

    $pidValue = [int] $state.pid
    if (Get-Command taskkill.exe -ErrorAction SilentlyContinue) {
        & cmd.exe /c "taskkill /PID $pidValue /T >NUL 2>NUL"
    }
    else {
        Stop-Process -Id $pidValue -ErrorAction SilentlyContinue
    }

    for ($i = 0; $i -lt 32; $i++) {
        if (-not $state.port -or -not (Test-Ready ([int] $state.port))) {
            Remove-Item -Force -ErrorAction SilentlyContinue $StateFile
            Write-Output "runtime: stopped"
            return
        }
        Start-Sleep -Milliseconds 250
    }

    if (Get-Command taskkill.exe -ErrorAction SilentlyContinue) {
        & cmd.exe /c "taskkill /PID $pidValue /T /F >NUL 2>NUL"
    }
    else {
        Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -Force -ErrorAction SilentlyContinue $StateFile
    Write-Output "runtime: stopped"
}

function Show-KernelLogs {
    foreach ($path in @($StdoutLog, $StderrLog)) {
        if (Test-Path $path) {
            Write-Output "==> $path <=="
            Get-Content -Tail 200 -Path $path
        }
    }
}

function Uninstall-CurrentVersion {
    if (Test-SourceLauncher) {
        throw "Refusing to uninstall from the source launcher. Use an installed deepcli command."
    }

    Invoke-WithLauncherLock {
        Stop-Runtime
        $binCmd = Join-Path $InstallDir "bin\deepcli.cmd"
        $binPs1 = Join-Path $InstallDir "bin\deepcli.ps1"
        Remove-LauncherShimsAfterExit @($binCmd, $binPs1)
        if ($ReleaseDir.StartsWith((Join-Path $InstallDir "releases"), [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $ReleaseDir
        }
        Remove-Item -Force -ErrorAction SilentlyContinue $StateFile
        Write-Output "DeepCLI uninstalled for this user."
        Write-Output "Preserved config/state:"
        Write-Output "  $ConfigDir"
        Write-Output "  $StateDir"
    }
}

function Remove-LauncherShimsAfterExit([string[]] $Paths) {
    $existing = @($Paths | Where-Object { Test-Path $_ })
    if ($existing.Count -eq 0) {
        return
    }
    $deleteScript = @(
        "Start-Sleep -Seconds 2"
        foreach ($path in $existing) {
            "Remove-Item -LiteralPath '$($path.Replace("'", "''"))' -Force -ErrorAction SilentlyContinue"
        }
    ) -join "; "
    Start-Process -WindowStyle Hidden -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        $deleteScript
    ) | Out-Null
}

function Invoke-Cli([string[]] $ForwardArgs) {
    $port = $DefaultPort
    for ($i = 0; $i -lt $ForwardArgs.Count; $i++) {
        if ($ForwardArgs[$i] -eq "--port" -and $i + 1 -lt $ForwardArgs.Count) {
            $port = [int] $ForwardArgs[$i + 1]
            break
        }
    }

    $runtimePort = Ensure-Runtime $port
    $token = Get-Token
    $layout = Resolve-Layout
    $cliCommand = @($layout.CliCommand)
    $cli = $cliCommand[0]
    $cliArgs = @()
    if ($cliCommand.Count -gt 1) {
        $cliArgs = $cliCommand[1..($cliCommand.Count - 1)]
    }
    if (-not (Test-Path $cli)) {
        throw "CLI command was not found: $cli"
    }

    $oldDeepCliToken = $env:DEEPCLI_TOKEN
    $oldMustangToken = $env:MUSTANG_TOKEN
    $oldKernelUrl = $env:KERNEL_URL
    $oldKernelHealthUrl = $env:KERNEL_HEALTH_URL
    try {
        $env:DEEPCLI_TOKEN = $token
        $env:MUSTANG_TOKEN = $token
        $env:KERNEL_URL = Get-WsUrl $runtimePort
        $env:KERNEL_HEALTH_URL = Get-HealthUrl $runtimePort
        Push-Location $layout.CliCwd
        try {
            & $cli @cliArgs @ForwardArgs
            exit $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
    }
    finally {
        $env:DEEPCLI_TOKEN = $oldDeepCliToken
        $env:MUSTANG_TOKEN = $oldMustangToken
        $env:KERNEL_URL = $oldKernelUrl
        $env:KERNEL_HEALTH_URL = $oldKernelHealthUrl
    }
}

$Command = if ($args.Count -gt 0) { $args[0] } else { "" }

switch ($Command) {
    "" { Invoke-Cli @() }
    { $_ -in @("--help", "-h", "help") } { Show-Usage }
    { $_ -in @("--uninstall", "uninstall") } { Uninstall-CurrentVersion }
    "status" { Show-Status }
    "stop" { Stop-Runtime }
    "restart" {
        Stop-Runtime
        Ensure-Runtime $DefaultPort | Out-Null
    }
    "kernel" {
        $Subcommand = if ($args.Count -gt 1) { $args[1] } else { "" }
        switch ($Subcommand) {
            "start" {
                $port = $DefaultPort
                if ($args.Count -gt 3 -and $args[2] -eq "--port") {
                    $port = [int] $args[3]
                }
                Ensure-Runtime $port | Out-Null
            }
            "logs" { Show-KernelLogs }
            default {
                Show-Usage
                exit 2
            }
        }
    }
    default { Invoke-Cli @args }
}
