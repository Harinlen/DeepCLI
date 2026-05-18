"""Install Obscura into DeepCLI's private package directory."""

from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile


def _deepcli_home() -> Path:
    return Path(os.getenv("DEEPCLI_HOME", Path.home() / ".deepcli")).expanduser()


def _asset_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "aarch64" if machine in {"aarch64", "arm64"} else "x86_64"

    if system == "linux":
        return f"obscura-{arch}-linux.tar.gz"
    if system == "darwin":
        return f"obscura-{arch}-macos.tar.gz"
    if system == "windows":
        return f"obscura-{arch}-windows.zip"
    raise RuntimeError(f"Unsupported platform for Obscura install: {system}/{machine}")


def _binary_name() -> str:
    return "obscura.exe" if platform.system().lower() == "windows" else "obscura"


def _extract_archive(archive: Path, target: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target)
        return
    with tarfile.open(archive) as tf:
        tf.extractall(target, filter="data")


def _find_binary(root: Path) -> Path:
    name = _binary_name()
    candidates = [path for path in root.rglob(name) if path.is_file()]
    if not candidates and name.endswith(".exe"):
        candidates = [path for path in root.rglob("obscura") if path.is_file()]
    if not candidates:
        raise RuntimeError(f"Obscura archive did not contain {name}")
    return candidates[0]


def main() -> None:
    asset = _asset_name()
    url = f"https://github.com/h4ckf0r0day/obscura/releases/latest/download/{asset}"
    bin_dir = _deepcli_home() / "packages" / "obscura" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="deepcli-obscura-") as tmp:
        tmp_dir = Path(tmp)
        archive = tmp_dir / asset
        urllib.request.urlretrieve(url, archive)  # noqa: S310 - fixed GitHub release URL
        extract_dir = tmp_dir / "extract"
        extract_dir.mkdir()
        _extract_archive(archive, extract_dir)

        binary = _find_binary(extract_dir)
        destination = bin_dir / _binary_name()
        shutil.copy2(binary, destination)
        mode = destination.stat().st_mode
        destination.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        for sidecar in extract_dir.rglob("obscura-worker*"):
            if sidecar.is_file():
                sidecar_dest = bin_dir / sidecar.name
                shutil.copy2(sidecar, sidecar_dest)
                sidecar_dest.chmod(sidecar_dest.stat().st_mode | stat.S_IXUSR)

    print(f"Installed Obscura to {bin_dir}")


if __name__ == "__main__":
    main()
