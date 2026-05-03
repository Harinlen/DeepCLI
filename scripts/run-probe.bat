@echo off
rem Start deepcli-probe (interactive ACP test client).

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."

cd /d "%REPO_ROOT%\src\probe"
uv run python -m probe %*
exit /b %ERRORLEVEL%
