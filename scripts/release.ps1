$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$ReleasePy = Join-Path $RepoRoot "scripts\release.py"

$Uv = Get-Command uv -ErrorAction SilentlyContinue
if ($Uv) {
    & $Uv.Source run python $ReleasePy @args
    exit $LASTEXITCODE
}

$Py = Get-Command py -ErrorAction SilentlyContinue
if ($Py) {
    & $Py.Source -3 $ReleasePy @args
    exit $LASTEXITCODE
}

$Python = Get-Command python -ErrorAction SilentlyContinue
if ($Python) {
    & $Python.Source $ReleasePy @args
    exit $LASTEXITCODE
}

$Python3 = Get-Command python3 -ErrorAction SilentlyContinue
if ($Python3) {
    & $Python3.Source $ReleasePy @args
    exit $LASTEXITCODE
}

throw "Python executable not found. Install uv or Python to use the release tool."
