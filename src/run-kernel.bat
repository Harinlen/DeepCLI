@echo off
:: Start Mustang through the Supervisor in dev mode.

if "%MUSTANG_FLAGS_PATH%"=="" (
  > "%~dp0.mustang-dev-flags.yaml" echo transport:
  >> "%~dp0.mustang-dev-flags.yaml" echo   stack: acp
  set "MUSTANG_FLAGS_PATH=%~dp0.mustang-dev-flags.yaml"
)

cd /d "%~dp0kernel"
if "%~1"=="" (
  uv run python -m kernel.supervisor --access-port 8200 --dev
) else (
  uv run python -m kernel.supervisor %*
)
