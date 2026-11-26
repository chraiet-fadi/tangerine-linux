"""Cross-desktop autostart and global OS shortcut integration for TangerineLinux."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tangerine_linux import __version__

SHORTCUT_SLOT = "tangerine-summon"
SHORTCUT_NAME = "Tangerine"
SHORTCUT_BINDING = "F9"

CINNAMON_SCHEMA = "org.cinnamon.desktop.keybindings"
CINNAMON_ENTRY_SCHEMA = f"{CINNAMON_SCHEMA}.custom-keybinding"
CINNAMON_ENTRY_PATH = f"/org/cinnamon/desktop/keybindings/custom-keybindings/{SHORTCUT_SLOT}/"

GNOME_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
GNOME_ENTRY_SCHEMA = f"{GNOME_SCHEMA}.custom-keybinding"
GNOME_ENTRY_PATH = f"/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/{SHORTCUT_SLOT}/"


def _get_executable_path() -> str:
    """Resolve the full executable path for desktop sessions without ~/.local/bin in PATH."""
    found = shutil.which("tangerine")
    if found:
        return found
    local_bin = Path.home() / ".local" / "bin" / "tangerine"
    if local_bin.exists():
        return str(local_bin)
    return f"{sys.executable} -m tangerine_linux.main"


def autostart_desktop_path() -> Path:
    """Return ~/.config/autostart/tangerine-linux.desktop."""
    return Path.home() / ".config" / "autostart" / "tangerine-linux.desktop"


def app_desktop_path() -> Path:
    """Return ~/.local/share/applications/tangerine-linux.desktop."""
    return Path.home() / ".local" / "share" / "applications" / "tangerine-linux.desktop"


def app_icon_path() -> Path:
    """Return ~/.local/share/icons/hicolor/128x128/apps/tangerine-linux.png."""
    return Path.home() / ".local" / "share" / "icons" / "hicolor" / "128x128" / "apps" / "tangerine-linux.png"


def config_dir_path() -> Path:
    """Return ~/.config/tangerine-linux."""
    return Path.home() / ".config" / "tangerine-linux"


def configured_marker_path() -> Path:
    """Return ~/.config/tangerine-linux/configured.json."""
    return config_dir_path() / "configured.json"


def is_autostart_enabled() -> bool:
    """Return True if the autostart .desktop entry exists."""
    return autostart_desktop_path().exists()


def enable_autostart(enabled: bool = True) -> bool:
    """Create or remove ~/.config/autostart/tangerine-linux.desktop for silent login launch."""
    desktop_file = autostart_desktop_path()
    if enabled:
        exec_path = _get_executable_path()
        desktop_file.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Version=1.0\n"
            "Name=TangerineLinux\n"
            "Comment=Offline radial file converter, quick actions, and local AI utility\n"
            f"Exec={exec_path} --no-orb\n"
            "Icon=tangerine-linux\n"
            "Terminal=false\n"
            "StartupNotify=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        desktop_file.write_text(content, encoding="utf-8")
        return True

    desktop_file.unlink(missing_ok=True)
    return False


def is_first_run() -> bool:
    """Return True if first-run setup has not yet been recorded."""
    return not configured_marker_path().exists()


def read_configured_state() -> dict[str, object]:
    """Read persisted integration state from configured.json, or {}."""
    marker = configured_marker_path()
    if not marker.exists():
        return {}
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def mark_configured(autostart: bool, shortcut_registered: bool) -> None:
    """Persist first-run/setup integration state to ~/.config/tangerine-linux/configured.json."""
    marker = configured_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": __version__,
        "autostart": autostart,
        "os_shortcut": shortcut_registered,
    }
    marker.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def clear_configured_marker() -> None:
    """Remove the first-run configuration marker if present."""
    configured_marker_path().unlink(missing_ok=True)


def remove_desktop_files_and_config() -> None:
    """Remove autostart, desktop launcher, app icon, and ~/.config/tangerine-linux/."""
    autostart_desktop_path().unlink(missing_ok=True)
    app_desktop_path().unlink(missing_ok=True)
    app_icon_path().unlink(missing_ok=True)
    shutil.rmtree(config_dir_path(), ignore_errors=True)


def _run_cmd(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _set_ok(args: list[str]) -> bool:
    res = _run_cmd(args)
    return res is not None and res.returncode == 0


def _parse_gsettings_list(raw: str) -> list[str]:
    text = raw.strip()
    if text.startswith("@as "):
        text = text[4:].strip()
    if not text or text == "[]":
        return []
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, (list, tuple)):
            return [str(item) for item in parsed]
    except (ValueError, SyntaxError):
        pass
    return []


def _current_desktop() -> str:
    return (
        os.environ.get("XDG_CURRENT_DESKTOP", "")
        + " "
        + os.environ.get("DESKTOP_SESSION", "")
    ).lower()


def _summon_command() -> str:
    return f"{_get_executable_path()} --summon"


def _register_cinnamon_shortcut() -> bool:
    gsettings = shutil.which("gsettings")
    if not gsettings:
        return False

    res = _run_cmd([gsettings, "get", CINNAMON_SCHEMA, "custom-list"])
    if res is None or res.returncode != 0:
        return False

    entries = _parse_gsettings_list(res.stdout)
    if SHORTCUT_SLOT not in entries:
        entries.append(SHORTCUT_SLOT)
        if not _set_ok([gsettings, "set", CINNAMON_SCHEMA, "custom-list", repr(entries)]):
            return False

    target = f"{CINNAMON_ENTRY_SCHEMA}:{CINNAMON_ENTRY_PATH}"
    return (
        _set_ok([gsettings, "set", target, "name", SHORTCUT_NAME])
        and _set_ok([gsettings, "set", target, "command", _summon_command()])
        and _set_ok([gsettings, "set", target, "binding", repr([SHORTCUT_BINDING])])
    )


def _unregister_cinnamon_shortcut() -> bool:
    gsettings = shutil.which("gsettings")
    if not gsettings:
        return False
    res = _run_cmd([gsettings, "get", CINNAMON_SCHEMA, "custom-list"])
    if res is None or res.returncode != 0:
        return False
    entries = [e for e in _parse_gsettings_list(res.stdout) if e != SHORTCUT_SLOT]
    target = f"{CINNAMON_ENTRY_SCHEMA}:{CINNAMON_ENTRY_PATH}"
    _run_cmd([gsettings, "reset-recursively", target])
    return _set_ok([gsettings, "set", CINNAMON_SCHEMA, "custom-list", repr(entries)])


def _register_gnome_shortcut() -> bool:
    gsettings = shutil.which("gsettings")
    if not gsettings:
        return False

    res = _run_cmd([gsettings, "get", GNOME_SCHEMA, "custom-keybindings"])
    if res is None or res.returncode != 0:
        return False

    entries = _parse_gsettings_list(res.stdout)
    if GNOME_ENTRY_PATH not in entries:
        entries.append(GNOME_ENTRY_PATH)
        if not _set_ok([gsettings, "set", GNOME_SCHEMA, "custom-keybindings", repr(entries)]):
            return False

    target = f"{GNOME_ENTRY_SCHEMA}:{GNOME_ENTRY_PATH}"
    return (
        _set_ok([gsettings, "set", target, "name", SHORTCUT_NAME])
        and _set_ok([gsettings, "set", target, "command", _summon_command()])
        and _set_ok([gsettings, "set", target, "binding", SHORTCUT_BINDING])
    )


def _unregister_gnome_shortcut() -> bool:
    gsettings = shutil.which("gsettings")
    if not gsettings:
        return False
    res = _run_cmd([gsettings, "get", GNOME_SCHEMA, "custom-keybindings"])
    if res is None or res.returncode != 0:
        return False
    entries = [e for e in _parse_gsettings_list(res.stdout) if e != GNOME_ENTRY_PATH]
    target = f"{GNOME_ENTRY_SCHEMA}:{GNOME_ENTRY_PATH}"
    _run_cmd([gsettings, "reset-recursively", target])
    return _set_ok([gsettings, "set", GNOME_SCHEMA, "custom-keybindings", repr(entries)])


def _register_xfce_shortcut() -> bool:
    xfconf = shutil.which("xfconf-query")
    if not xfconf:
        return False
    cmd = _summon_command()
    prop = f"/commands/custom/{SHORTCUT_BINDING}"
    if _set_ok([xfconf, "-c", "xfce4-keyboard-shortcuts", "-p", prop, "-s", cmd]):
        return True
    return _set_ok([
        xfconf, "-c", "xfce4-keyboard-shortcuts", "-p", prop,
        "-n", "-t", "string", "-s", cmd,
    ])


def _unregister_xfce_shortcut() -> bool:
    xfconf = shutil.which("xfconf-query")
    if not xfconf:
        return False
    prop = f"/commands/custom/{SHORTCUT_BINDING}"
    return _set_ok([xfconf, "-c", "xfce4-keyboard-shortcuts", "-p", prop, "-r"])


def _register_kde_shortcut() -> bool:
    kwrite = shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")
    if not kwrite:
        return False

    apps_dir = Path.home() / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    shortcut_desktop = apps_dir / f"{SHORTCUT_SLOT}.desktop"
    shortcut_desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={SHORTCUT_NAME}\n"
        f"Exec={_summon_command()}\n"
        "Icon=tangerine-linux\n"
        "NoDisplay=true\n"
        "StartupNotify=false\n",
        encoding="utf-8",
    )

    return _set_ok([
        kwrite,
        "--file", "kglobalshortcutsrc",
        "--group", "services",
        "--group", f"{SHORTCUT_SLOT}.desktop",
        "--key", "_launch",
        "F9,none,Tangerine",
    ])


def _unregister_kde_shortcut() -> bool:
    kwrite = shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")
    shortcut_desktop = Path.home() / ".local" / "share" / "applications" / f"{SHORTCUT_SLOT}.desktop"
    shortcut_desktop.unlink(missing_ok=True)
    if not kwrite:
        return False
    return _set_ok([
        kwrite,
        "--file", "kglobalshortcutsrc",
        "--group", "services",
        "--group", f"{SHORTCUT_SLOT}.desktop",
        "--key", "_launch",
        "--delete",
    ])


def register_default_shortcut() -> bool:
    """Bind F9 -> '<full_path> --summon' natively for the active desktop."""
    desktop = _current_desktop()
    if "cinnamon" in desktop:
        return _register_cinnamon_shortcut()
    if any(name in desktop for name in ("gnome", "ubuntu", "unity")):
        return _register_gnome_shortcut()
    if "xfce" in desktop:
        return _register_xfce_shortcut()
    if any(name in desktop for name in ("kde", "plasma")):
        return _register_kde_shortcut()
    return False


def unregister_default_shortcut() -> bool:
    """Remove the native OS F9 shortcut if configured."""
    desktop = _current_desktop()
    if "cinnamon" in desktop:
        return _unregister_cinnamon_shortcut()
    if any(name in desktop for name in ("gnome", "ubuntu", "unity")):
        return _unregister_gnome_shortcut()
    if "xfce" in desktop:
        return _unregister_xfce_shortcut()
    if any(name in desktop for name in ("kde", "plasma")):
        return _unregister_kde_shortcut()
    return False
