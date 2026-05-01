@echo off
:: Start Mustang through the Supervisor in dev mode.
cd /d "%~dp0kernel"
if "%~1"=="" (
  uv run python -m kernel.supervisor --access-port 8200 --dev
) else (
  uv run python -m kernel.supervisor %*
)
