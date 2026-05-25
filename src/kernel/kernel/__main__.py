"""Allow running the supervised Kernel as ``python -m kernel``."""

from __future__ import annotations

import argparse
from pathlib import Path

from kernel.core.paths import user_state_dir
from kernel.supervisor.runtime import (
    SupervisorConfig,
    SupervisorRuntime,
    install_signal_handlers,
)


def main() -> None:
    """Entry point for ``python -m kernel``."""
    parser = argparse.ArgumentParser(description="DeepCLI Kernel")
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    parser.add_argument("--host", default="127.0.0.1", help="Access Router host")
    parser.add_argument("--port", type=int, default=8200, help="Access Router port")
    parser.add_argument("--access-port", type=int, help="Alias for --port")
    parser.add_argument("--state-dir", help="Supervisor state directory")
    parser.add_argument("--workspace", default=str(Path.cwd()), help="Runtime workspace")
    parser.add_argument("--prompt-backend", choices=("compat", "router"), default="router")
    parser.add_argument("--dev", action="store_true", help="Enable INFO-level logging")
    args = parser.parse_args()

    if args.version:
        from kernel import __version__

        print(f"deepcli kernel {__version__}")
        return

    access_port = args.access_port if args.access_port is not None else args.port
    runtime = SupervisorRuntime(
        SupervisorConfig(
            access_port=access_port,
            state_dir=Path(args.state_dir) if args.state_dir else user_state_dir(),
            workspace=Path(args.workspace),
            host=args.host,
            dev=args.dev,
            prompt_backend=args.prompt_backend,
        )
    )
    install_signal_handlers(runtime)
    try:
        runtime.start()
        runtime.wait()
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
