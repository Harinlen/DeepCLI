"""Install-page asset helpers for WebBridge."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def extension_root() -> Path:
    """Return the repository extension source directory."""

    return Path(__file__).resolve().parents[7] / "web-bridge-extension"


def unpacked_extension_path() -> Path:
    """Return the Chrome unpacked extension directory."""

    return extension_root() / "dist" / "chrome"


def zip_path() -> Path:
    """Return the WebBridge extension zip path, creating it if needed."""

    root = extension_root()
    dist = root / "dist"
    unpacked = unpacked_extension_path()
    archive = dist / "deepcli-web-bridge.zip"
    dist.mkdir(parents=True, exist_ok=True)
    if not unpacked.exists():
        return archive
    with ZipFile(archive, "w", ZIP_DEFLATED) as zf:
        for path in sorted(unpacked.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(unpacked))
    return archive


def install_page_html(status_json: str) -> str:
    """Render the local WebBridge install wizard."""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>DeepCLI WebBridge Install</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 32px; max-width: 880px; }}
    code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; }}
    .step {{ border-left: 4px solid #2563eb; padding: 10px 16px; margin: 14px 0; }}
    button, a.button {{ padding: 8px 12px; margin-right: 8px; }}
    pre {{ white-space: pre-wrap; background: #f9fafb; padding: 12px; }}
  </style>
</head>
<body>
  <h1>DeepCLI WebBridge</h1>
  <p>Install the Chrome extension by loading the local unpacked extension folder.</p>
  <section class="step"><strong>1. Build ready</strong>
    <p>Keep this page open. After the extension is loaded, it will auto-detect this page and pair itself.</p>
  </section>
  <section class="step"><strong>2. Open Chrome extensions</strong>
    <p><a class="button" href="chrome://extensions" target="_blank" rel="noreferrer">Open chrome://extensions</a>
    <button onclick="copyText('chromeUrl')">Copy fallback</button></p>
    <p>If Chrome blocks the button, paste this into the address bar:
      <code id="chromeUrl">chrome://extensions</code>
    </p>
  </section>
  <section class="step"><strong>3. Enable Developer mode</strong>
    <p>Turn on <strong>Developer mode</strong> in the top-right corner.</p>
  </section>
  <section class="step"><strong>4. Load unpacked</strong>
    <p>Click <strong>Load unpacked</strong> and choose:</p>
    <code id="unpackedPath"></code>
    <button onclick="copyText('unpackedPath')">Copy path</button>
    <p>When Chrome finishes loading the extension, this status panel should switch to connected automatically.</p>
  </section>
  <p><a class="button" href="/web-bridge/deepcli-web-bridge.zip">Download zip fallback</a>
  <button onclick="pairAgain()">Pair again</button>
  <button onclick="resetPairing()">Reset pairing</button></p>
  <h2>Status</h2>
  <pre id="status">{status_json}</pre>
  <script>
    async function refresh() {{
      const res = await fetch('/web-bridge/status.json');
      const data = await res.json();
      document.getElementById('status').textContent = JSON.stringify(data, null, 2);
      document.getElementById('unpackedPath').textContent = data.unpackedPath || '';
    }}
    async function pairAgain() {{ await fetch('/web-bridge/pair', {{method: 'POST'}}); await refresh(); }}
    async function resetPairing() {{ await fetch('/web-bridge/reset', {{method: 'POST'}}); await refresh(); }}
    async function copyText(id) {{ await navigator.clipboard.writeText(document.getElementById(id).textContent); }}
    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>"""


__all__ = ["extension_root", "install_page_html", "unpacked_extension_path", "zip_path"]
