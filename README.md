<div align="center">

<img src="assets/tangerine.png" alt="TangerineLinux" width="84" height="84" />

# TangerineLinux

*A lightweight, offline radial file converter and quick-actions dial for Linux.*

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Linux%20(X11%20%2F%20Wayland)-FCC624?style=flat-square&logo=linux&logoColor=black)
![License](https://img.shields.io/badge/License-MIT-FF6B00?style=flat-square)

<!-- Replace with a short screen recording GIF of the radial wheel in action -->
![TangerineLinux Radial Wheel](assets/demo.gif)

</div>

---

## Why TangerineLinux

- **Keyboard-first or drag-and-drop workflow:** Summon the translucent radial dial under your cursor with `Super + Shift + T`, or drag files onto the `56x56` floating citrus orb on the right edge of your screen.
- **Dual-mode wheel (`Tab` or `Right-Click`):** Switch immediately between **Convert Mode** (target formats) and **Actions Mode** (compression, PDF tools, metadata stripping, background removal, OCR).
- **100% offline and local:** All processing runs on your machine using Pillow, FFmpeg, `pypdf`, Tesseract OCR, and `rembg` (`u2netp`)—no cloud uploads, no API keys.
- **Ephemeral AI memory design:** Background removal and OCR execute inside isolated `multiprocessing` (`spawn`) child processes that exit immediately after finishing so the Linux kernel reclaims 100% of ONNX/AI RAM right away—keeping main-process idle RSS around `~80 MB` (and `~98 MB` after AI tasks, measured via `/proc/self/status`).

---

## Supported Formats & Quick Actions

| Category | Convert Mode | Actions Mode |
| :--- | :--- | :--- |
| **Images**<br>`.png` `.jpg` `.jpeg` `.webp` `.bmp` `.tiff` | `WEBP`, `JPG`, `PNG`, `AVIF`, `ICO`, `TIFF` | `Remove BG` (local AI), `OCR Text` (saves `.txt` + copies to clipboard), `Compress`, `To PDF`, `Collage`, `Strip EXIF` |
| **Audio**<br>`.wav` `.mp3` `.flac` `.ogg` `.m4a` `.opus` | `MP3`, `FLAC`, `OGG`, `M4A`, `WAV`, `OPUS` | `Compress 128k`, `Normalize`, `Trim Silence`, `Mono Mix`, `Extract Info`, `Fade In/Out` |
| **Video**<br>`.mp4` `.mkv` `.mov` `.avi` `.webm` | `MP4`, `GIF`, `WEBM`, `MP3 (Audio)`, `MKV`, `MOV` | `Compress H.264`, `720p Resize`, `Mute Audio`, `First Frame JPG`, `Speed 2x`, `Strip Metadata` |
| **PDF**<br>`.pdf` | — | `Merge PDFs`, `Split Pages`, `Extract Text`, `Compress PDF`, `Rotate 90°`, `Metadata Info` |

---

## Installation

### Step 1 — System Dependencies

Install `ffmpeg` and `tesseract-ocr` for audio/video processing and local OCR:

- **Ubuntu / Linux Mint / Debian:**
  ```bash
  sudo apt update && sudo apt install -y ffmpeg tesseract-ocr tesseract-ocr-eng tesseract-ocr-ara tesseract-ocr-fra
  ```
- **Fedora:**
  ```bash
  sudo dnf install -y ffmpeg tesseract tesseract-langpack-eng
  ```
- **Arch Linux:**
  ```bash
  sudo pacman -S ffmpeg tesseract tesseract-data-eng
  ```

### Step 2 — Install TangerineLinux

#### Primary Method (`uv`)

```bash
uv tool install git+https://github.com/chraiet-fadi/tangerine-linux.git
```

> **Tip:** If `tangerine` is not recognized in your shell after installation, add `~/.local/bin` to your `PATH`:
> ```bash
> uv tool update-shell
> ```
> Then open a new terminal session.

#### Alternative Method (Includes Desktop Menu Icon & Launcher)

```bash
git clone https://github.com/chraiet-fadi/tangerine-linux.git
cd tangerine-linux
chmod +x install.sh
./install.sh
```

---

## Usage & Keyboard Workflow

On your first launch, `tangerine` automatically enables silent background login autostart (`~/.config/autostart/tangerine-linux.desktop` running `tangerine --no-orb`) and binds `Super + Shift + T` to `tangerine --summon` natively on **Cinnamon**, **GNOME**, **XFCE**, and **KDE Plasma** (falling back to an in-process `pynput` listener on other window managers without double-triggering).

**Daily workflow:**
1. Copy one or more files in your file manager with `Ctrl + C` (or drag files directly onto the floating orb).
2. Press `Super + Shift + T` to open the radial wheel centered at your cursor.
3. Click or drop onto any slice (`Tab` or `Right-Click` toggles between `CONVERT` and `ACTIONS`; `Esc` or clicking the center hub cancels).

**Notes:**
- Output files are written alongside the source file with safe auto-incrementing names (`_converted`, `_nobg`, `_ocr`, etc.) and never overwrite originals.
- The first `Remove BG` run downloads the `u2netp` model (~4.7 MB) once into `~/.rembg/`; every subsequent run is completely offline.

<details>
<summary>Architecture & Manual Window Manager Keybinding</summary>

- **Single-instance IPC:** `tangerine --summon` connects to the `tangerine-linux-ipc` `QLocalServer` socket and signals the active background process instead of spawning a second Qt instance.
- **Tiling WMs (i3 / Sway / Hyprland):** Add a binding in your WM config pointing `Super+Shift+T` to `tangerine --summon`.
- **Two-window X11 design:** `FloatingOrb` (`56x56`) and `RadialWheel` (`320x320`) keep fixed sizes with a cached frosted-glass backdrop `QPixmap` and `QPainter`-only spring animations to avoid X11 XDND flicker.

</details>

---

## CLI Reference

| Command | Description |
| :--- | :--- |
| `tangerine` | Start the floating orb and system tray (runs first-run setup if needed). |
| `tangerine --summon` | Summon the radial wheel at the cursor (uses clipboard files or opens ready to drop). |
| `tangerine --no-orb` | Start minimized to the system tray and hotkey listener without showing the orb. |
| `tangerine --doctor` | Inspect Python, PyQt6, `ffmpeg`, `tesseract` languages, and display server status. |
| `tangerine --setup` | Re-apply login autostart (`--no-orb`) and the `Super + Shift + T` OS shortcut. |
| `tangerine --uninstall-integration` | Remove the autostart `.desktop` entry and custom OS shortcut. |

---

## License & Author

Released under the [MIT License](LICENSE) by **Fadi Chraiet** ([@chraiet-fadi](https://github.com/chraiet-fadi)).
