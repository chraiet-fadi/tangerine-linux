"""Distro-aware system dependency detection and install command helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Sequence

OS_RELEASE_PATH = Path("/etc/os-release")


def detect_package_manager(os_release_text: str | None = None) -> str:
    """Detect the Linux package manager ('apt', 'dnf', 'pacman', or 'generic')."""
    if os_release_text is None and OS_RELEASE_PATH.exists():
        try:
            os_release_text = OS_RELEASE_PATH.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            os_release_text = ""

    tokens: set[str] = set()
    for line in (os_release_text or "").splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip().upper()
        if key in ("ID", "ID_LIKE"):
            cleaned = val.strip().strip('"').strip("'").lower()
            tokens.update(cleaned.split())

    if tokens & {"ubuntu", "debian", "linuxmint", "pop", "elementary", "zorin", "kali", "raspbian"}:
        return "apt"
    if tokens & {"fedora", "rhel", "centos", "rocky", "almalinux", "nobara"}:
        return "dnf"
    if tokens & {"arch", "manjaro", "endeavouros", "garuda", "cachyos", "artix"}:
        return "pacman"

    if shutil.which("apt-get") or shutil.which("apt"):
        return "apt"
    if shutil.which("dnf"):
        return "dnf"
    if shutil.which("pacman"):
        return "pacman"
    return "generic"


def install_command_for(
    tools: Sequence[str],
    pkg_manager: str | None = None,
) -> str:
    """Return a copy-pasteable install command for the requested missing tools."""
    mgr = pkg_manager or detect_package_manager()
    normalized = []
    for t in tools:
        if t in ("ffmpeg", "tesseract") and t not in normalized:
            normalized.append(t)
    if not normalized:
        normalized = ["ffmpeg", "tesseract"]

    pkg_map: dict[str, dict[str, list[str]]] = {
        "apt": {
            "ffmpeg": ["ffmpeg"],
            "tesseract": ["tesseract-ocr", "tesseract-ocr-eng"],
        },
        "dnf": {
            "ffmpeg": ["ffmpeg"],
            "tesseract": ["tesseract", "tesseract-langpack-eng"],
        },
        "pacman": {
            "ffmpeg": ["ffmpeg"],
            "tesseract": ["tesseract", "tesseract-data-eng"],
        },
    }

    if mgr in pkg_map:
        pkgs: list[str] = []
        for tool in normalized:
            pkgs.extend(pkg_map[mgr][tool])
        if mgr == "apt":
            return f"sudo apt install -y {' '.join(pkgs)}"
        if mgr == "dnf":
            return f"sudo dnf install -y {' '.join(pkgs)}"
        if mgr == "pacman":
            return f"sudo pacman -S {' '.join(pkgs)}"

    human_names = [
        "ffmpeg" if t == "ffmpeg" else "tesseract-ocr"
        for t in normalized
    ]
    return f"Install {' and '.join(human_names)} using your distribution's package manager"


def find_ffmpeg() -> str | None:
    """Return the resolved path to ffmpeg if on PATH, else None."""
    return shutil.which("ffmpeg")


def find_tesseract() -> str | None:
    """Return the resolved path to tesseract if on PATH, else None."""
    return shutil.which("tesseract")


def missing_system_tools() -> list[str]:
    """Return missing external system tools from ['ffmpeg', 'tesseract']."""
    missing: list[str] = []
    if find_ffmpeg() is None:
        missing.append("ffmpeg")
    if find_tesseract() is None:
        missing.append("tesseract")
    return missing


def list_tesseract_langs() -> list[str]:
    """Return installed Tesseract OCR language codes, or [] if unavailable."""
    tesseract_bin = find_tesseract()
    if tesseract_bin is None:
        return []
    try:
        proc = subprocess.run(
            [tesseract_bin, "--list-langs"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        langs: list[str] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line or line.lower().startswith("list of available languages"):
                continue
            langs.append(line)
        return langs
    except (OSError, subprocess.TimeoutExpired):
        return []
