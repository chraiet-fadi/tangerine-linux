#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

echo "=== TangerineLinux Installer ==="

# 1. Check / install required system packages (ffmpeg, tesseract)
missing_sys=()
command -v ffmpeg >/dev/null 2>&1 || missing_sys+=("ffmpeg")
command -v tesseract >/dev/null 2>&1 || missing_sys+=("tesseract")

if [[ ${#missing_sys[@]} -gt 0 ]]; then
    echo "Missing system dependencies: ${missing_sys[*]}"
    if command -v apt-get >/dev/null 2>&1; then
        echo "Detected apt-get. Installing ffmpeg, tesseract-ocr, tesseract-ocr-eng, libnotify-bin..."
        sudo apt-get update && sudo apt-get install -y ffmpeg tesseract-ocr tesseract-ocr-eng libnotify-bin
    elif command -v dnf >/dev/null 2>&1; then
        echo "Detected dnf. Installing ffmpeg, tesseract, tesseract-langpack-eng, libnotify..."
        sudo dnf install -y ffmpeg tesseract tesseract-langpack-eng libnotify
    elif command -v pacman >/dev/null 2>&1; then
        echo "Detected pacman. Installing ffmpeg, tesseract, tesseract-data-eng, libnotify..."
        sudo pacman -Sy --noconfirm ffmpeg tesseract tesseract-data-eng libnotify
    else
        echo "Warning: Unsupported package manager. Please install 'ffmpeg' and 'tesseract-ocr' manually."
    fi
else
    echo "System dependencies OK (ffmpeg & tesseract found)."
fi

# 2. Install CLI globally
PYTHON_FOR_ICON=""
if command -v uv >/dev/null 2>&1; then
    echo "Installing tangerine CLI via uv tool install..."
    uv tool install --force --reinstall .
    TOOL_BIN_DIR="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
    if [[ -x "$TOOL_BIN_DIR/tangerine" ]]; then
        PYTHON_FOR_ICON="$(head -n1 "$TOOL_BIN_DIR/tangerine" | sed 's/^#!//')"
    fi
elif command -v pipx >/dev/null 2>&1; then
    echo "Installing tangerine CLI via pipx..."
    pipx install --force .
    PYTHON_FOR_ICON="$HOME/.local/pipx/venvs/tangerine-linux/bin/python"
else
    echo "Installing tangerine CLI via pip --user..."
    python3 -m pip install --user --upgrade .
    PYTHON_FOR_ICON="python3"
fi

# 3. Generate 128x128 PNG application icon
ICON_DIR="$HOME/.local/share/icons/hicolor/128x128/apps"
ICON_PATH="$ICON_DIR/tangerine-linux.png"
mkdir -p "$ICON_DIR"

echo "Generating 128x128 application icon at $ICON_PATH..."
ICON_SCRIPT=$(cat <<'PYEOF'
import sys
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication
from tangerine_linux.ui.radial_wheel import paint_tangerine_gem

app = QApplication([])
size = 128
img = QImage(size, size, QImage.Format.Format_ARGB32)
img.fill(Qt.GlobalColor.transparent)
painter = QPainter(img)
paint_tangerine_gem(painter, float(size), scale=1.24, opacity=1.0, with_shadow=False)
painter.end()
ok = img.save(sys.argv[1], "PNG")
sys.exit(0 if ok else 1)
PYEOF
)

if [[ -n "$PYTHON_FOR_ICON" && -x "$PYTHON_FOR_ICON" ]]; then
    QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" "$PYTHON_FOR_ICON" -c "$ICON_SCRIPT" "$ICON_PATH"
elif command -v uv >/dev/null 2>&1; then
    QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" uv run python -c "$ICON_SCRIPT" "$ICON_PATH"
else
    QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" python3 -c "$ICON_SCRIPT" "$ICON_PATH"
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi

# 4. Create desktop entry
APPS_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APPS_DIR/tangerine-linux.desktop"
mkdir -p "$APPS_DIR"

cat >"$DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=TangerineLinux
GenericName=Radial File Converter & Quick Actions
Comment=Offline radial file converter, quick actions, and local AI utility
Exec=tangerine
Icon=tangerine-linux
Terminal=false
Categories=Utility;AudioVideo;Graphics;
StartupNotify=false
EOF

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi
echo "Created desktop entry at $DESKTOP_FILE"

# 5. Optional OS keyboard shortcut setup (Super+Shift+T -> tangerine --summon)
configure_gsettings_shortcut() {
    local base_schema="$1"
    local list_key="$2"
    local entry_prefix="$3"
    local slot_name="tangerine-summon"
    local slot_path="${entry_prefix}${slot_name}/"

    local current_list
    current_list="$(gsettings get "$base_schema" "$list_key" 2>/dev/null || echo "[]")"
    if [[ "$current_list" != *"'$slot_name'"* ]]; then
        if [[ "$current_list" == "@as []" || "$current_list" == "[]" ]]; then
            new_list="['$slot_name']"
        else
            new_list="${current_list%]*}, '$slot_name']"
        fi
        gsettings set "$base_schema" "$list_key" "$new_list"
    fi

    gsettings set "${base_schema}.custom-keybinding:${slot_path}" name "Summon TangerineLinux"
    gsettings set "${base_schema}.custom-keybinding:${slot_path}" command "tangerine --summon"
    gsettings set "${base_schema}.custom-keybinding:${slot_path}" binding "['<Super><Shift>t']" 2>/dev/null \
        || gsettings set "${base_schema}.custom-keybinding:${slot_path}" binding "<Super><Shift>t"
}

DESKTOP_ENV="${XDG_CURRENT_DESKTOP:-}"
if command -v gsettings >/dev/null 2>&1; then
    if [[ "$DESKTOP_ENV" == *"Cinnamon"* ]]; then
        if configure_gsettings_shortcut "org.cinnamon.desktop.keybindings" "custom-list" "/org/cinnamon/desktop/keybindings/custom-keybindings/" 2>/dev/null; then
            echo "Configured Cinnamon shortcut: Super+Shift+T -> tangerine --summon"
        fi
    elif [[ "$DESKTOP_ENV" == *"GNOME"* ]]; then
        if configure_gsettings_shortcut "org.gnome.settings-daemon.plugins.media-keys" "custom-keybindings" "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/" 2>/dev/null; then
            echo "Configured GNOME shortcut: Super+Shift+T -> tangerine --summon"
        fi
    fi
fi

echo ""
echo "=== Installation Complete ==="
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo "Note: Add \$HOME/.local/bin to your PATH to run 'tangerine' from any terminal."
fi
echo "Usage:"
echo "  tangerine            # Start floating citrus orb + system tray"
echo "  tangerine --summon   # Summon radial wheel at cursor"
echo "  tangerine --no-orb   # Start minimized to tray / hotkey only"
echo ""
echo "Keyboard Shortcut Setup (any Desktop Environment):"
echo "  Bind 'Super+Shift+T' (or any shortcut) to: tangerine --summon"
echo "  (On KDE Plasma: System Settings -> Shortcuts -> Custom Shortcuts -> Add Command)"
