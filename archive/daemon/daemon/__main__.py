"""Allow running daemon as ``python -m daemon``."""

import sys


def main() -> None:
    """Entry point for ``python -m daemon``.

    Config is loaded once inside the FastAPI lifespan, not here.
    We only read the minimal daemon host/port defaults for uvicorn.
    """
    if "--version" in sys.argv:
        from daemon import __version__

        print(f"mustang-daemon {__version__}")
        return

    from daemon.config.defaults import DEFAULT_DAEMON
    from daemon.config.loader import CONFIG_PATH

    # Read host/port from YAML if it exists, otherwise use defaults
    host = DEFAULT_DAEMON.host
    port = DEFAULT_DAEMON.port

    if CONFIG_PATH.exists():
        import yaml

        try:
            raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
            daemon_cfg = raw.get("daemon", {})
            host = daemon_cfg.get("host", host)
            port = daemon_cfg.get("port", port)
        except Exception:  # nosec B110
            pass  # Fall through to defaults; lifespan will report the real error

    import uvicorn

    uvicorn.run(
        "daemon.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
