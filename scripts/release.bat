@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
set "RELEASE_PY=%REPO_ROOT%\scripts\release.py"

where uv >nul 2>nul
if not errorlevel 1 (
    uv run python "%RELEASE_PY%" %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%RELEASE_PY%" %*
    exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if not errorlevel 1 (
    python "%RELEASE_PY%" %*
    exit /b %ERRORLEVEL%
)

where python3 >nul 2>nul
if not errorlevel 1 (
    python3 "%RELEASE_PY%" %*
    exit /b %ERRORLEVEL%
)

echo Python executable not found. Install uv or Python to use the release tool. 1>&2
exit /b 1
