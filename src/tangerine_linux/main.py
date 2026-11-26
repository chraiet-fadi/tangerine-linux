"""TangerineLinux CLI entry point, single-instance IPC, System Tray, and Global Hotkey."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import signal
import sys
from pathlib import Path
from typing import Sequence
from urllib.parse import unquote, urlparse

from PyQt6.QtCore import (
    PYQT_VERSION_STR,
    QCoreApplication,
    QObject,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QAction, QCursor, QIcon, QPainter, QPixmap
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from tangerine_linux import __version__
from tangerine_linux.core.converters import send_notification
from tangerine_linux.core.deps import (
    detect_package_manager,
    find_ffmpeg,
    find_tesseract,
    install_command_for,
    list_tesseract_langs,
    missing_system_tools,
)
from tangerine_linux.core.integration import (
    _get_executable_path,
    app_desktop_path,
    app_icon_path,
    autostart_desktop_path,
    config_dir_path,
    enable_autostart,
    is_autostart_enabled,
    is_first_run,
    mark_configured,
    register_default_shortcut,
    remove_desktop_files_and_config,
    unregister_default_shortcut,
)
from tangerine_linux.core.router import route_files
from tangerine_linux.ui.radial_wheel import FloatingOrb, RadialWheel, paint_tangerine_gem

IPC_SOCKET_NAME = "tangerine-linux-ipc"


def verify_ffmpeg() -> str | None:
    """Return the path to ffmpeg if available on PATH, else None."""
    return find_ffmpeg()


def verify_tesseract() -> str | None:
    """Return the path to tesseract if available on PATH, else None."""
    return find_tesseract()


def create_tangerine_tray_icon() -> QIcon:
    """Render a crisp 64x64 6-segment rounded tangerine citrus-gem icon for the tray."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    paint_tangerine_gem(painter, float(size), scale=1.22, opacity=1.0, with_shadow=False)
    painter.end()

    return QIcon(pixmap)


def extract_clipboard_file_paths() -> list[Path]:
    """Extract local file paths from the Qt clipboard (URLs or file:// text lines)."""
    clipboard = QApplication.clipboard()
    if clipboard is None:
        return []
    mime = clipboard.mimeData()
    if mime is None:
        return []

    paths: list[Path] = []
    if mime.hasUrls():
        for url in mime.urls():
            if url.isLocalFile():
                local = url.toLocalFile()
                if local:
                    paths.append(Path(local))

    if not paths and mime.hasText():
        for raw_line in mime.text().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("file://"):
                parsed = urlparse(line)
                local_path = unquote(parsed.path)
                if local_path:
                    paths.append(Path(local_path))
            else:
                qurl = QUrl(line)
                if qurl.isLocalFile() and qurl.toLocalFile():
                    paths.append(Path(qurl.toLocalFile()))

    return paths


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the tangerine command."""
    parser = argparse.ArgumentParser(
        prog="tangerine",
        description="TangerineLinux — offline radial file converter, quick actions, and local AI utility.",
    )
    parser.add_argument(
        "--summon",
        action="store_true",
        help="Summon the radial wheel at the cursor (uses clipboard files if present, or opens ready to drop).",
    )
    parser.add_argument(
        "--no-orb",
        action="store_true",
        help="Start minimized to the system tray and global hotkey without showing the floating orb.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Check Python, PyQt6, FFmpeg, Tesseract OCR, and display session diagnostics.",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Configure login autostart (--no-orb) and the default Super+Shift+T shortcut.",
    )
    parser.add_argument(
        "--uninstall",
        "--uninstall-integration",
        dest="uninstall",
        action="store_true",
        help="Stop running instance and remove autostart, desktop entry, icon, config, and OS shortcut.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def _detect_display_session() -> str:
    session = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    wayland = os.environ.get("WAYLAND_DISPLAY", "").strip()
    x11 = os.environ.get("DISPLAY", "").strip()
    if session == "wayland" or (wayland and not x11):
        return f"Wayland ({wayland or 'active'})"
    if session == "x11" or x11:
        return f"X11 ({x11 or 'active'})"
    return "Unknown / Headless"


def run_doctor() -> int:
    """Print diagnostic report for runtime environment and external binaries."""
    ffmpeg_path = find_ffmpeg()
    tesseract_path = find_tesseract()
    pkg_mgr = detect_package_manager()
    session = _detect_display_session()

    print(f"TangerineLinux Diagnostics (v{__version__})")
    print("-" * 44)
    print(f"Python         : {platform.python_version()}")
    print(f"PyQt6          : {PYQT_VERSION_STR}")
    print(f"Display Server : {session}")
    print(f"Package Manager: {pkg_mgr}")

    if ffmpeg_path:
        print(f"ffmpeg         : [OK] {ffmpeg_path}")
    else:
        print(f"ffmpeg         : [MISSING] Install with: {install_command_for(['ffmpeg'], pkg_mgr)}")

    if tesseract_path:
        langs = list_tesseract_langs()
        lang_str = ", ".join(langs) if langs else "none detected"
        print(f"tesseract      : [OK] {tesseract_path}")
        print(f"OCR languages  : {lang_str}")
    else:
        print(f"tesseract      : [MISSING] Install with: {install_command_for(['tesseract'], pkg_mgr)}")

    missing = missing_system_tools()
    if missing:
        print("-" * 44)
        print(f"Install missing dependencies: {install_command_for(missing, pkg_mgr)}")
        return 1

    print("-" * 44)
    print("All core system dependencies are installed.")
    return 0


def run_setup() -> int:
    """Apply autostart (--no-orb) and OS Super+Shift+T shortcut, then print status."""
    exec_path = _get_executable_path()
    autostart_ok = enable_autostart(True)
    shortcut_ok = register_default_shortcut()
    mark_configured(autostart=autostart_ok, shortcut_registered=shortcut_ok)

    print("TangerineLinux Desktop Integration")
    print("-" * 44)
    print(f"Executable           : {exec_path}")
    print(f"Autostart (--no-orb) : [OK] {autostart_desktop_path()}")
    if shortcut_ok:
        print(f"OS Shortcut          : [OK] Super+Shift+T -> {exec_path} --summon")
    else:
        print(
            "OS Shortcut          : [FALLBACK] Unsupported DE for native shortcut; "
            "in-process Super+Shift+T listener will be used while running."
        )
    return 0


def run_uninstall() -> int:
    """Stop any running instance and remove all desktop integration and config files."""
    if QCoreApplication.instance() is None:
        _qt_app = QCoreApplication(sys.argv[:1])  # noqa: F841

    stopped = notify_running_instance(b"QUIT\n")
    remove_desktop_files_and_config()
    shortcut_removed = unregister_default_shortcut()

    print("TangerineLinux Uninstall Cleanup")
    print("-" * 44)
    print(f"Background instance  : {'[STOPPED]' if stopped else '[NOT RUNNING]'}")
    print(f"Autostart entry      : [REMOVED] {autostart_desktop_path()}")
    print(f"Desktop launcher     : [REMOVED] {app_desktop_path()}")
    print(f"Application icon     : [REMOVED] {app_icon_path()}")
    print(f"Config directory     : [REMOVED] {config_dir_path()}")
    print(
        f"OS Shortcut          : {'[REMOVED]' if shortcut_removed else '[SKIPPED]'} "
        "Super+Shift+T"
    )
    print("-" * 44)
    if shutil.which("uv"):
        print("To remove the CLI package, run: uv tool uninstall tangerine-linux")
    elif shutil.which("pipx"):
        print("To remove the CLI package, run: pipx uninstall tangerine-linux")
    else:
        print("To remove the CLI package, run: pip uninstall tangerine-linux")
    return 0


def _notify_missing_dependencies_on_startup() -> None:
    """Send a single non-blocking startup notification if ffmpeg or tesseract is missing."""
    missing = missing_system_tools()
    if not missing:
        return

    feature_notes = []
    if "ffmpeg" in missing:
        feature_notes.append("Audio/Video features need ffmpeg")
    if "tesseract" in missing:
        feature_notes.append("OCR Text needs tesseract-ocr")

    cmd = install_command_for(missing)
    body = f"{'; '.join(feature_notes)}. Install: {cmd} (run 'tangerine --doctor' for details)"
    print(f"Warning: {body}", file=sys.stderr)
    send_notification("TangerineLinux: Optional Dependencies Missing", body, urgency="normal")


def notify_running_instance(message: bytes = b"SUMMON\n", timeout_ms: int = 250) -> bool:
    """Send an IPC command to an already-running instance. Return True if delivered."""
    socket = QLocalSocket()
    socket.connectToServer(IPC_SOCKET_NAME)
    if not socket.waitForConnected(timeout_ms):
        return False
    socket.write(message)
    socket.flush()
    socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    return True


class HotkeyBridge(QObject):
    """Thread-safe signal bridge from pynput listener thread to Qt main thread."""

    triggered = pyqtSignal()


class AppController(QObject):
    """Main-thread controller for tray actions, IPC summoning, and hotkey handling."""

    def __init__(
        self,
        wheel: RadialWheel,
        orb: FloatingOrb,
        toggle_orb_action: QAction,
        autostart_action: QAction,
        tray_icon: QSystemTrayIcon,
        ipc_server: QLocalServer | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._wheel = wheel
        self._orb = orb
        self._toggle_orb_action = toggle_orb_action
        self._autostart_action = autostart_action
        self._tray_icon = tray_icon
        self._ipc_server = ipc_server
        self._hotkey_listener: object | None = None

    def set_hotkey_listener(self, listener: object | None) -> None:
        self._hotkey_listener = listener

    @pyqtSlot()
    def sync_toggle_action(self) -> None:
        self._toggle_orb_action.setChecked(self._orb.is_enabled())
        self._autostart_action.setChecked(is_autostart_enabled())

    @pyqtSlot(bool)
    def on_toggle_orb(self, checked: bool) -> None:
        if self._wheel.isVisible() and not checked:
            self._orb.set_enabled(False)
        else:
            self._orb.set_enabled(checked)
        self.sync_toggle_action()

    @pyqtSlot(bool)
    def on_toggle_autostart(self, checked: bool) -> None:
        enable_autostart(checked)
        self.sync_toggle_action()

    @pyqtSlot()
    def summon_wheel_at_cursor(self) -> None:
        """Summon RadialWheel at QCursor.pos() with clipboard files, or ready to drop."""
        paths = extract_clipboard_file_paths()
        cursor_pos = QCursor.pos()
        if paths and route_files(paths) is not None:
            self._orb.hide()
            if self._wheel.open_with_files(paths, anchor_pos=cursor_pos):
                return

        if self._wheel.isVisible() and not self._wheel._is_closing:
            self._wheel.cancel_and_close()
            return

        self._orb.hide()
        self._wheel.open_empty_at_cursor(cursor_pos)

    @pyqtSlot()
    def handle_hotkey_summon_or_toggle(self) -> None:
        """Global hotkey handler: summon at cursor if clipboard has files, else toggle orb."""
        paths = extract_clipboard_file_paths()
        if paths and route_files(paths) is not None:
            self._orb.hide()
            if self._wheel.open_with_files(paths, anchor_pos=QCursor.pos()):
                return
        if self._wheel.isVisible():
            self._wheel.cancel_and_close()
        else:
            self._orb.set_enabled(not self._orb.is_enabled())
        self.sync_toggle_action()

    @pyqtSlot()
    def on_ipc_connection(self) -> None:
        if self._ipc_server is None:
            return
        while self._ipc_server.hasPendingConnections():
            client = self._ipc_server.nextPendingConnection()
            if client is None:
                continue
            client.waitForReadyRead(150)
            payload = bytes(client.readAll()).decode("utf-8", errors="ignore").strip().upper()
            client.disconnectFromServer()
            client.deleteLater()
            if "QUIT" in payload:
                QTimer.singleShot(0, QApplication.quit)
                return
            if "SUMMON" in payload or payload == "":
                self.summon_wheel_at_cursor()

    @pyqtSlot()
    def cleanup(self) -> None:
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()  # type: ignore[attr-defined]
            except Exception:
                pass
        if self._ipc_server is not None:
            try:
                self._ipc_server.close()
                QLocalServer.removeServer(IPC_SOCKET_NAME)
            except Exception:
                pass
        self._tray_icon.hide()


def main(argv: Sequence[str] | None = None) -> int:
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    args = parse_args(argv)

    if args.doctor:
        return run_doctor()
    if args.setup:
        return run_setup()
    if args.uninstall:
        return run_uninstall()

    app = QApplication(sys.argv)
    app.setApplicationName("TangerineLinux")
    app.setQuitOnLastWindowClosed(False)

    # Single-instance check via QLocalSocket
    if notify_running_instance(b"SUMMON\n"):
        return 0

    if is_first_run():
        enable_autostart(True)
        os_shortcut_registered = register_default_shortcut()
        mark_configured(autostart=True, shortcut_registered=os_shortcut_registered)
        send_notification(
            "TangerineLinux is ready",
            "TangerineLinux is ready in the background. Press Super+Shift+T anytime to summon the wheel.",
        )
    else:
        os_shortcut_registered = register_default_shortcut()

    _notify_missing_dependencies_on_startup()

    QLocalServer.removeServer(IPC_SOCKET_NAME)
    ipc_server = QLocalServer(app)
    if not ipc_server.listen(IPC_SOCKET_NAME):
        print(
            f"Warning: Could not start IPC server '{IPC_SOCKET_NAME}': {ipc_server.errorString()}",
            file=sys.stderr,
        )

    wheel = RadialWheel()
    orb = FloatingOrb(wheel)
    if args.no_orb:
        orb.set_enabled(False)
    else:
        orb.show()

    tray_icon = QSystemTrayIcon(create_tangerine_tray_icon(), parent=app)
    tray_icon.setToolTip("TangerineLinux — Drag files or press Super+Shift+T")

    tray_menu = QMenu()
    summon_action = QAction("Summon Wheel at Cursor (Super+Shift+T)", tray_menu)
    toggle_orb_action = QAction("Toggle Floating Orb", tray_menu)
    toggle_orb_action.setCheckable(True)
    toggle_orb_action.setChecked(orb.is_enabled())
    autostart_action = QAction("Start on Login (Background)", tray_menu)
    autostart_action.setCheckable(True)
    autostart_action.setChecked(is_autostart_enabled())
    quit_action = QAction("Quit", tray_menu)

    tray_menu.addAction(summon_action)
    tray_menu.addAction(toggle_orb_action)
    tray_menu.addAction(autostart_action)
    tray_menu.addSeparator()
    tray_menu.addAction(quit_action)
    tray_icon.setContextMenu(tray_menu)

    controller = AppController(
        wheel,
        orb,
        toggle_orb_action,
        autostart_action,
        tray_icon,
        ipc_server=ipc_server,
        parent=app,
    )

    ipc_server.newConnection.connect(controller.on_ipc_connection)
    tray_menu.aboutToShow.connect(controller.sync_toggle_action)
    summon_action.triggered.connect(controller.summon_wheel_at_cursor)
    toggle_orb_action.triggered.connect(controller.on_toggle_orb)
    autostart_action.triggered.connect(controller.on_toggle_autostart)
    quit_action.triggered.connect(app.quit)
    tray_icon.show()

    hotkey_bridge = HotkeyBridge(app)
    hotkey_bridge.triggered.connect(
        controller.handle_hotkey_summon_or_toggle,
        Qt.ConnectionType.QueuedConnection,
    )

    # Avoid double-triggering: only run pynput Super+Shift+T listener if OS shortcut wasn't registered
    if not os_shortcut_registered:
        try:
            from pynput import keyboard

            hotkey_listener = keyboard.GlobalHotKeys(
                {"<cmd>+<shift>+t": hotkey_bridge.triggered.emit}
            )
            hotkey_listener.start()
            controller.set_hotkey_listener(hotkey_listener)
        except Exception as exc:
            print(
                f"Warning: Could not register global hotkey Super+Shift+T: {exc}",
                file=sys.stderr,
            )

    if args.summon:
        QTimer.singleShot(0, controller.summon_wheel_at_cursor)

    app.aboutToQuit.connect(controller.cleanup)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
