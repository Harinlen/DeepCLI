@echo off
setlocal EnableExtensions
set "PS_EXE=pwsh"
where pwsh >nul 2>nul
if errorlevel 1 set "PS_EXE=powershell.exe"
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-cli.ps1" %*
exit /b %ERRORLEVEL%
