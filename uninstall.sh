#!/usr/bin/env bash
set -euo pipefail

echo "=== TangerineLinux Uninstaller ==="

TANGERINE_BIN="$(command -v tangerine || echo "$HOME/.local/bin/tangerine")"
if [[ -x "$TANGERINE_BIN" ]]; then
    echo "Stopping running instance and removing desktop integration..."
    "$TANGERINE_BIN" --uninstall
else
    echo "tangerine binary not found; cleaning up desktop integration paths..."
    rm -f "$HOME/.config/autostart/tangerine-linux.desktop" \
          "$HOME/.local/share/applications/tangerine-linux.desktop" \
          "$HOME/.local/share/applications/tangerine-summon.desktop" \
          "$HOME/.local/share/icons/hicolor/128x128/apps/tangerine-linux.png"
    rm -rf "$HOME/.config/tangerine-linux"
fi

if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q "tangerine-linux"; then
    echo "Removing tangerine-linux via uv tool uninstall..."
    uv tool uninstall tangerine-linux
elif command -v pipx >/dev/null 2>&1 && pipx list 2>/dev/null | grep -q "tangerine-linux"; then
    echo "Removing tangerine-linux via pipx uninstall..."
    pipx uninstall tangerine-linux
elif python3 -m pip show tangerine-linux >/dev/null 2>&1; then
    echo "Removing tangerine-linux via pip uninstall..."
    python3 -m pip uninstall -y tangerine-linux
else
    echo "Package already removed or not installed via uv/pipx/pip."
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

echo "TangerineLinux has been completely uninstalled."
