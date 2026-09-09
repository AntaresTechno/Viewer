"""Native custom-scheme launcher installed by the protocol bridge.

The OS invokes this file with the original URI.  It validates the scheme,
optionally starts a local Viewer backend, and opens the authenticated Web
confirmation page in the user's default browser.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import quote, urlsplit


def bridge_url(base_url: str, uri: str, schemes: list[str]) -> str:
    uri = str(uri or "").strip()
    if not uri or len(uri) > 8192 or any(ord(char) < 0x20 for char in uri):
        raise ValueError("invalid protocol URI")
    parsed = urlsplit(uri)
    allowed = {str(item).lower() for item in schemes}
    if parsed.scheme.lower() not in allowed:
        raise ValueError("unsupported protocol scheme")
    return base_url.rstrip("/") + "/protocol?uri=" + quote(uri, safe="")


def _viewer_is_ready(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(  # noqa: S310 - validated local/config URL
            base_url.rstrip("/") + "/api/health", timeout=0.7
        ) as response:
            return 200 <= response.status < 500
    except Exception:  # noqa: BLE001
        return False


def _start_local_viewer(config: dict) -> None:
    if not config.get("auto_start") or _viewer_is_ready(config["base_url"]):
        return
    python = str(config.get("python_executable") or sys.executable)
    backend_dir = str(config.get("backend_dir") or "")
    if not backend_dir or not Path(backend_dir).is_dir():
        return
    parsed = urlsplit(config["base_url"])
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    command = [
        python, "-m", "uvicorn", "app.main:app",
        "--host", host, "--port", str(port),
    ]
    kwargs = {
        "cwd": backend_dir,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **kwargs)  # noqa: S603 - fixed argv
    except Exception:  # noqa: BLE001
        return
    for _ in range(24):
        if _viewer_is_ready(config["base_url"]):
            break
        time.sleep(0.25)


def open_protocol(config_path: str, uri: str) -> str:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    target = bridge_url(
        str(config["base_url"]), uri, list(config.get("schemes") or [])
    )
    _start_local_viewer(config)
    webbrowser.open(target, new=2)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Viewer native protocol bridge")
    parser.add_argument("--config", required=True)
    parser.add_argument("uri")
    args = parser.parse_args()
    try:
        open_protocol(args.config, args.uri)
    except Exception:  # noqa: BLE001 - protocol launchers must fail quietly
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
