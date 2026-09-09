"""Per-user native protocol registration for Windows, macOS and Linux."""
from __future__ import annotations

import json
import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


_APP_ID = "io.viewer.protocolbridge"
_APP_NAME = "Viewer Protocol Bridge"


class NativeRegistrationError(RuntimeError):
    pass


def _platform() -> str:
    return platform.system().lower()


def _validate_base_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if len(value) > 2048:
        raise NativeRegistrationError("Viewer 地址过长")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise NativeRegistrationError("Viewer 地址格式错误") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise NativeRegistrationError("Viewer 地址必须是 HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise NativeRegistrationError("Viewer 地址不能包含账号、查询参数或片段")
    return value


def _paths(system: str | None = None) -> tuple[Path, Path]:
    system = system or _platform()
    home = Path.home()
    if system == "windows":
        root = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        state = root / "Viewer" / "ProtocolBridge"
        target = state
    elif system == "darwin":
        state = home / "Library" / "Application Support" / "Viewer" / "ProtocolBridge"
        target = home / "Applications" / f"{_APP_NAME}.app"
    else:
        state = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "viewer" / "protocol-bridge"
        target = home / ".local" / "share" / "applications" / "viewer-protocol-bridge.desktop"
    return state, target


def _config(base_url: str, schemes: list[str]) -> dict:
    parsed = urlsplit(base_url)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    return {
        "version": 1,
        "base_url": base_url,
        "schemes": schemes,
        "auto_start": bool(local and parsed.scheme == "http"),
        "python_executable": sys.executable,
        "backend_dir": str(Path(__file__).resolve().parents[3]),
    }


def _write_config(state: Path, config: dict) -> Path:
    state.mkdir(parents=True, exist_ok=True)
    path = state / "config.json"
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _launcher() -> Path:
    return Path(__file__).with_name("native_launcher.py").resolve()


def _windows_python() -> Path:
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    return pythonw if pythonw.exists() else executable


def _read_windows_registration(scheme: str) -> dict[str, str]:
    import winreg

    root_path = r"Software\Classes" + "\\" + scheme
    result: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root_path) as key:
            try:
                result["description"] = str(winreg.QueryValueEx(key, "")[0])
            except FileNotFoundError:
                pass
            try:
                result["urlProtocol"] = str(
                    winreg.QueryValueEx(key, "URL Protocol")[0]
                )
            except FileNotFoundError:
                pass
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, root_path + r"\shell\open\command"
        ) as key:
            result["command"] = str(winreg.QueryValueEx(key, "")[0])
    except (FileNotFoundError, OSError):
        pass
    return result


def _windows_is_ours(scheme: str, config_path: Path) -> bool:
    command = _read_windows_registration(scheme).get("command", "")
    return str(_launcher()).lower() in command.lower() \
        and str(config_path).lower() in command.lower()


def _restore_windows_registration(scheme: str, previous: dict[str, str]) -> None:
    if not previous.get("command"):
        return
    import winreg

    root_path = r"Software\Classes" + "\\" + scheme
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root_path) as key:
        winreg.SetValueEx(
            key, "", 0, winreg.REG_SZ,
            previous.get("description", f"URL:{scheme} Protocol"),
        )
        winreg.SetValueEx(
            key, "URL Protocol", 0, winreg.REG_SZ,
            previous.get("urlProtocol", ""),
        )
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, root_path + r"\shell\open\command"
    ) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, previous["command"])


def _install_windows(config_path: Path, schemes: list[str]) -> None:
    import winreg

    prefix = r"Software\Classes"
    command = subprocess.list2cmdline([
        str(_windows_python()), str(_launcher()),
        "--config", str(config_path),
    ]) + ' "%1"'
    for scheme in schemes:
        key_path = prefix + "\\" + scheme
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"URL:Viewer {scheme} Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, key_path + r"\shell\open\command"
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)


def _delete_windows_tree(path: str) -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            children = []
            index = 0
            while True:
                try:
                    children.append(winreg.EnumKey(key, index))
                    index += 1
                except OSError:
                    break
        for child in children:
            _delete_windows_tree(path + "\\" + child)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        return


def _install_macos(config_path: Path, target: Path, schemes: list[str]) -> None:
    compiler = shutil.which("osacompile")
    if compiler is None:
        raise NativeRegistrationError("系统缺少 osacompile，无法创建协议应用")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    command_prefix = " ".join(shlex.quote(item) for item in [
        sys.executable, str(_launcher()), "--config", str(config_path),
    ])
    script = (
        "on open location protocol_url\n"
        f"do shell script {json.dumps(command_prefix + ' ')} & "
        "quoted form of protocol_url & \" >/dev/null 2>&1 &\"\n"
        "end open location"
    )
    result = subprocess.run(
        [compiler, "-o", str(target), "-e", script],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise NativeRegistrationError("创建 macOS 协议应用失败")
    plist_path = target / "Contents" / "Info.plist"
    with plist_path.open("rb") as file:
        info = plistlib.load(file)
    info.update({
        "CFBundleIdentifier": _APP_ID,
        "CFBundleName": _APP_NAME,
        "LSBackgroundOnly": True,
        "CFBundleURLTypes": [{
            "CFBundleURLName": _APP_ID,
            "CFBundleURLSchemes": schemes,
        }],
    })
    with plist_path.open("wb") as file:
        plistlib.dump(info, file)
    codesign = shutil.which("codesign")
    if codesign:
        subprocess.run(
            [codesign, "--force", "--deep", "--sign", "-", str(target)],
            check=False,
        )
    lsregister = Path(
        "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
        "LaunchServices.framework/Support/lsregister"
    )
    if lsregister.exists():
        subprocess.run([str(lsregister), "-f", str(target)], check=False)


def _desktop_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _linux_default_handlers(schemes: list[str]) -> dict[str, str]:
    xdg_mime = shutil.which("xdg-mime")
    if not xdg_mime:
        return {}
    result: dict[str, str] = {}
    for scheme in schemes:
        query = subprocess.run(
            [xdg_mime, "query", "default", f"x-scheme-handler/{scheme}"],
            capture_output=True, text=True, check=False,
        )
        value = query.stdout.strip()
        if query.returncode == 0 and value:
            result[scheme] = value
    return result


def _install_linux(config_path: Path, target: Path, schemes: list[str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    mime_types = "".join(f"x-scheme-handler/{scheme};" for scheme in schemes)
    command = " ".join([
        _desktop_quote(sys.executable),
        _desktop_quote(str(_launcher())),
        "--config", _desktop_quote(str(config_path)), "%u",
    ])
    target.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={_APP_NAME}\n"
        f"Exec={command}\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        f"MimeType={mime_types}\n",
        encoding="utf-8",
    )
    target.chmod(0o755)
    xdg_mime = shutil.which("xdg-mime")
    if xdg_mime:
        for scheme in schemes:
            subprocess.run(
                [xdg_mime, "default", target.name, f"x-scheme-handler/{scheme}"],
                check=False,
            )
    update_db = shutil.which("update-desktop-database")
    if update_db:
        subprocess.run([update_db, str(target.parent)], check=False)


def _read_config(state: Path) -> dict:
    try:
        value = json.loads((state / "config.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def native_status() -> dict:
    system = _platform()
    supported = system in {"windows", "darwin", "linux"}
    state, target = _paths(system)
    config = _read_config(state)
    if system == "windows":
        config_path = state / "config.json"
        schemes = list(config.get("schemes") or [])
        installed = bool(schemes) and all(
            _windows_is_ours(scheme, config_path) for scheme in schemes
        )
    else:
        installed = target.exists()
    return {
        "platform": system,
        "supported": supported,
        "installed": bool(supported and installed),
        "schemes": list(config.get("schemes") or []),
        "baseUrl": str(config.get("base_url") or ""),
        "autoStart": bool(config.get("auto_start")),
        "target": str(target),
        "scope": "current-server-user",
    }


def install_native_bridge(base_url: str, schemes: list[str]) -> dict:
    system = _platform()
    if system not in {"windows", "darwin", "linux"}:
        raise NativeRegistrationError(f"暂不支持当前系统：{system}")
    normalized = sorted({str(item).lower() for item in schemes if str(item)})
    if not normalized:
        raise NativeRegistrationError("当前没有已注册的自定义协议")
    base_url = _validate_base_url(base_url)
    state, target = _paths(system)
    old_config = _read_config(state)
    old_previous = old_config.get("previous_handlers", {})
    if not isinstance(old_previous, dict):
        old_previous = {}
    config = _config(base_url, normalized)
    if system == "windows":
        previous = {}
        for scheme in normalized:
            if _windows_is_ours(scheme, state / "config.json"):
                previous[scheme] = old_previous.get(scheme, {})
            else:
                previous[scheme] = _read_windows_registration(scheme)
        config["previous_handlers"] = previous
    elif system == "linux":
        config["previous_handlers"] = (
            old_previous if target.exists()
            else _linux_default_handlers(normalized)
        )
    config_path = _write_config(state, config)
    try:
        if system == "windows":
            _install_windows(config_path, normalized)
        elif system == "darwin":
            _install_macos(config_path, target, normalized)
        else:
            _install_linux(config_path, target, normalized)
    except Exception:
        # Keep the config for diagnostics/retry, but never report a partial
        # platform registration as successful.
        raise
    return native_status()


def uninstall_native_bridge() -> dict:
    system = _platform()
    state, target = _paths(system)
    config = _read_config(state)
    schemes = list(config.get("schemes") or ["legado", "yuedu"])
    if system == "windows":
        for scheme in schemes:
            if _windows_is_ours(scheme, state / "config.json"):
                _delete_windows_tree(r"Software\Classes" + "\\" + scheme)
                previous = config.get("previous_handlers", {}).get(scheme, {})
                if isinstance(previous, dict):
                    _restore_windows_registration(scheme, previous)
    elif system == "darwin":
        lsregister = Path(
            "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
            "LaunchServices.framework/Support/lsregister"
        )
        if target.exists() and lsregister.exists():
            subprocess.run([str(lsregister), "-u", str(target)], check=False)
        if target.exists():
            shutil.rmtree(target)
    elif system == "linux":
        if target.exists():
            target.unlink()
        xdg_mime = shutil.which("xdg-mime")
        previous = config.get("previous_handlers", {})
        if xdg_mime and isinstance(previous, dict):
            for scheme, desktop_name in previous.items():
                if desktop_name:
                    subprocess.run([
                        xdg_mime, "default", str(desktop_name),
                        f"x-scheme-handler/{scheme}",
                    ], check=False)
        update_db = shutil.which("update-desktop-database")
        if update_db:
            subprocess.run([update_db, str(target.parent)], check=False)
    else:
        raise NativeRegistrationError(f"暂不支持当前系统：{system}")
    if state.exists():
        shutil.rmtree(state)
    return native_status()
