# TangerineLinux

A lightweight, 100% offline radial file converter, quick-actions dial, and local AI utility for Linux — inspired by Tangerine for Mac and built with Python, PyQt6, Pillow, FFmpeg, `pypdf`, `rembg`, and Tesseract OCR.

## Features

- **Frosted Acrylic Radial Wheel + Citrus Floating Orb:**
  - Docks discreetly as a translucent `56×56` citrus-gem orb on the right edge of your screen.
  - Drag any supported file onto the orb (or press your global shortcut) to spring open a `320×320` frosted-glass 6-slice radial wheel.
  - Supports both **zero-click drag-and-drop** and **click-to-convert** interaction.
- **Dual-Mode Context Routing (`CONVERT` & `ACTIONS`):**
  - Switch modes instantly with `Tab`, right-click anywhere on the wheel, or clicking the hub's mode badge/dots.
  - **Convert Mode (`#FF5E00`):**
    - **Images** (`.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.tiff`): `WEBP`, `JPG`, `PNG`, `AVIF`, `ICO`, `TIFF`
    - **Audio** (`.wav`, `.mp3`, `.flac`, `.ogg`, `.m4a`, `.opus`): `MP3`, `FLAC`, `OGG`, `M4A`, `WAV`, `OPUS`
    - **Video** (`.mp4`, `.mkv`, `.mov`, `.avi`, `.webm`): `MP4`, `GIF`, `WEBM`, `MP3 (Audio)`, `MKV`, `MOV`
  - **Quick Actions Mode (`#7C3AED` badge / Tangerine glow):**
    - **Images:** `Remove BG` (local `u2netp`), `OCR Text` (copies to clipboard + saves `.txt`), `Compress`, `To PDF`, `Collage` (1–4 image grid), `Strip EXIF`
    - **Audio:** `Compress 128k`, `Normalize`, `Trim Silence`, `Mono Mix`, `Extract Info`, `Fade In/Out`
    - **Video:** `Compress H.264`, `720p Resize`, `Mute Audio`, `First Frame JPG`, `Speed 2x`, `Strip Metadata`
    - **PDF** (auto-opens in Actions Mode): `Merge PDFs`, `Split Pages`, `Extract Text`, `Compress PDF`, `Rotate 90°`, `Metadata Info`
- **Zero-Idle-RAM Ephemeral AI Architecture:**
  - `rembg` (`onnxruntime` + `u2netp`) and `pytesseract` are never imported into the main GUI process.
  - AI actions execute inside isolated `multiprocessing` (`spawn`) child processes that exit immediately upon completion so the Linux kernel reclaims 100% of AI/ONNX memory right away.
  - `pypdf` is lazy-loaded only when PDF actions are invoked.
- **Single-Instance CLI, System Tray & Global Hotkey:**
  - Built-in `QSystemTrayIcon` and `Ctrl+Alt+X` global hotkey.
  - `QLocalServer` / `QLocalSocket` single-instance IPC lets OS keyboard shortcuts summon the active wheel instantly via `tangerine --summon` with zero duplicate processes.

## Installation

### 1. System Dependencies

Install `ffmpeg` and `tesseract-ocr` for audio/video conversions and local OCR:

- **Ubuntu / Debian / Linux Mint:**
  ```bash
  sudo apt install -y ffmpeg tesseract-ocr tesseract-ocr-eng libnotify-bin
  ```
- **Fedora:**
  ```bash
  sudo dnf install -y ffmpeg tesseract tesseract-langpack-eng libnotify
  ```
- **Arch Linux / Manjaro:**
  ```bash
  sudo pacman -S ffmpeg tesseract tesseract-data-eng libnotify
  ```

### 2. One-Command Install

Install `tangerine` globally straight from GitHub using `uv`:

```bash
uv tool install git+https://github.com/chraiet-fadi/tangerine-linux.git
```

> **Note:** If `tangerine` is not found in your terminal after installing, add `uv`'s tool bin directory (`~/.local/bin`) to your `PATH`:
> ```bash
> uv tool update-shell
> ```
> Then open a new terminal (or `source ~/.bashrc` / `~/.zshrc`).

#### Optional: Full Desktop Integration (`./install.sh`)

If you also want a `.desktop` launcher, application icon, and automatic `Super+Shift+T` shortcut setup on Cinnamon/GNOME:

```bash
git clone https://github.com/chraiet-fadi/tangerine-linux.git
cd tangerine-linux
./install.sh
```

### 3. Run & Bind a Global Shortcut

```bash
tangerine            # Start floating citrus orb + system tray
tangerine --summon   # Summon radial wheel at cursor (uses clipboard files or opens ready to drop)
tangerine --no-orb   # Start minimized to system tray & hotkey only
tangerine --doctor   # Check Python, PyQt6, ffmpeg, tesseract, and display server
tangerine --help     # Show CLI options
```

To summon TangerineLinux instantly from anywhere on **any** desktop environment (Cinnamon, GNOME, KDE Plasma, XFCE, Hyprland, Sway, i3):
1. Open your desktop environment's **Keyboard Shortcuts** settings.
2. Add a custom shortcut (e.g., `Super+Shift+T`) with command:
   ```bash
   tangerine --summon
   ```
3. When `tangerine` is already running, `tangerine --summon` sends `"SUMMON"` over the `tangerine-linux-ipc` local socket to pop the radial wheel centered at your cursor immediately without spawning duplicate processes.

## Architecture Highlights

- **`src/tangerine_linux/main.py`:** `argparse` CLI, `QLocalServer`/`QLocalSocket` single-instance IPC, `QSystemTrayIcon`, and thread-safe `pynput` hotkey bridge (`AppController`).
- **`src/tangerine_linux/ui/radial_wheel.py`:** Fixed-size two-window X11 design (`FloatingOrb` `56×56`, `RadialWheel` `320×320`) with pre-rendered frosted-acrylic backdrop pixmap caching, spring `QPainter` animations, and `@pyqtSlot` queued signal relays.
- **`src/tangerine_linux/core/router.py`:** Extension detection, dual-mode slice mapping, and mixed-drop filtering.
- **`src/tangerine_linux/core/converters.py`:** Async `QThreadPool` Pillow & FFmpeg conversion workers with non-overwriting output naming (`_converted`, `_converted_1`).
- **`src/tangerine_linux/core/actions.py`:** Async quick actions engine with lazy `pypdf` imports and ephemeral `spawn` child processes for `Remove BG` and `OCR Text`.

## License

MIT License — Copyright (c) 2026 Fadi Chraiet. See [LICENSE](LICENSE).
