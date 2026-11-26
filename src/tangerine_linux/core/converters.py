"""Asynchronous media and image converters for TangerineLinux."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image, ImageOps
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from tangerine_linux.core.deps import find_ffmpeg, install_command_for
from tangerine_linux.core.router import MediaCategory

FORMAT_TO_EXTENSION: dict[str, str] = {
    # Image
    "WEBP": ".webp",
    "JPG": ".jpg",
    "PNG": ".png",
    "AVIF": ".avif",
    "ICO": ".ico",
    "TIFF": ".tiff",
    # Audio
    "MP3": ".mp3",
    "FLAC": ".flac",
    "OGG": ".ogg",
    "M4A": ".m4a",
    "WAV": ".wav",
    "OPUS": ".opus",
    # Video
    "MP4": ".mp4",
    "GIF": ".gif",
    "WEBM": ".webm",
    "MP3 (Audio)": ".mp3",
    "MKV": ".mkv",
    "MOV": ".mov",
}

FFMPEG_ARGS: dict[str, list[str]] = {
    # Audio
    "MP3": ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
    "MP3 (Audio)": ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
    "FLAC": ["-vn", "-c:a", "flac"],
    "OGG": ["-vn", "-c:a", "libvorbis", "-q:a", "5"],
    "M4A": ["-vn", "-c:a", "aac", "-b:a", "192k"],
    "WAV": ["-vn", "-c:a", "pcm_s16le"],
    "OPUS": ["-vn", "-c:a", "libopus", "-b:a", "128k"],
    # Video
    "MP4": [
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
    ],
    "MKV": [
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
    ],
    "MOV": [
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
    ],
    "WEBM": [
        "-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0",
        "-c:a", "libopus", "-b:a", "128k",
    ],
    "GIF": [
        "-vf", "fps=15,scale='min(720,iw)':-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
        "-loop", "0",
    ],
}


def unique_output_path(
    parent: Path,
    stem: str,
    ext: str,
    reserved: set[Path] | None = None,
) -> Path:
    """Return a non-overwriting output path inside parent."""
    normalized_ext = ext if ext.startswith(".") else f".{ext}"
    candidate = parent / f"{stem}{normalized_ext}"
    counter = 1
    while candidate.exists() or (reserved is not None and candidate in reserved):
        candidate = parent / f"{stem}_{counter}{normalized_ext}"
        counter += 1

    if reserved is not None:
        reserved.add(candidate)
    return candidate


def build_output_path(
    input_path: Path,
    target_format: str,
    reserved: set[Path] | None = None,
) -> Path:
    """Return a non-overwriting output path next to the original file.

    Uses `<stem><ext>` when available, otherwise `<stem>_converted<ext>`,
    `<stem>_converted_1<ext>`, etc.
    """
    ext = FORMAT_TO_EXTENSION[target_format]
    parent = input_path.parent
    stem = input_path.stem
    candidate = parent / f"{stem}{ext}"
    if candidate.exists() or (reserved is not None and candidate in reserved):
        return unique_output_path(parent, f"{stem}_converted", ext, reserved=reserved)
    if reserved is not None:
        reserved.add(candidate)
    return candidate


def _flatten_alpha_to_white(img: Image.Image) -> Image.Image:
    """Composite an image with transparency onto a solid white RGB canvas."""
    rgba = img.convert("RGBA")
    background = Image.new("RGB", rgba.size, (255, 255, 255))
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background


def convert_image(src: Path, dst: Path, target_format: str) -> None:
    """Convert an image file using Pillow."""
    with Image.open(src) as opened:
        img = ImageOps.exif_transpose(opened)
        out_img: Image.Image | None = None
        try:
            if target_format == "JPG":
                has_alpha = img.mode in ("RGBA", "LA", "PA") or (
                    img.mode == "P" and "transparency" in img.info
                )
                out_img = _flatten_alpha_to_white(img) if has_alpha else img.convert("RGB")
                out_img.save(dst, format="JPEG", quality=92, optimize=True)
            elif target_format == "WEBP":
                out_img = img if img.mode in ("RGB", "RGBA") else img.convert("RGBA")
                out_img.save(dst, format="WEBP", quality=90)
            elif target_format == "PNG":
                out_img = img if img.mode in ("RGB", "RGBA", "L", "LA") else img.convert("RGBA")
                out_img.save(dst, format="PNG", optimize=True)
            elif target_format == "AVIF":
                out_img = img if img.mode in ("RGB", "RGBA") else img.convert("RGBA")
                out_img.save(dst, format="AVIF", quality=85)
            elif target_format == "ICO":
                out_img = img.convert("RGBA")
                if out_img.width > 256 or out_img.height > 256:
                    out_img.thumbnail((256, 256), Image.Resampling.LANCZOS)
                max_dim = max(out_img.width, out_img.height)
                sizes = [(s, s) for s in (256, 128, 64, 32, 16) if s <= max_dim]
                if not sizes:
                    sizes = [(out_img.width, out_img.height)]
                out_img.save(dst, format="ICO", sizes=sizes)
            elif target_format == "TIFF":
                out_img = img if img.mode in ("RGB", "RGBA", "L") else img.convert("RGBA")
                out_img.save(dst, format="TIFF", compression="tiff_deflate")
            else:
                raise ValueError(f"Unsupported image target format: {target_format}")
        finally:
            if out_img is not None and out_img is not img:
                out_img.close()
            if img is not opened:
                img.close()


def probe_duration_seconds(src: Path) -> float | None:
    """Return media duration in seconds via ffprobe, or None if unavailable."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(src.resolve()),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            duration = float(proc.stdout.strip())
            return duration if duration > 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return None


def run_ffmpeg_command(
    src: Path,
    dst: Path,
    extra_args: Sequence[str],
    on_progress: Callable[[float], None] | None = None,
) -> None:
    """Run ffmpeg on src -> dst with progress reporting and cleanup on failure."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            f"FFmpeg is required for audio/video tasks. Install it with: {install_command_for(['ffmpeg'])}"
        )

    src_abs = str(src.resolve())
    dst_abs = str(dst.resolve())
    duration = probe_duration_seconds(src)
    cmd = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-progress", "pipe:1",
        "-n",
        "-i", src_abs,
        *extra_args,
        dst_abs,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                line = line.strip()
                if duration and line.startswith("out_time_us=") and on_progress is not None:
                    try:
                        out_us = int(line.split("=", 1)[1])
                        ratio = min(1.0, max(0.0, (out_us / 1_000_000.0) / duration))
                        on_progress(ratio)
                    except ValueError:
                        pass
                elif line == "progress=end" and on_progress is not None:
                    on_progress(1.0)
        _, stderr_text = proc.communicate()
    except Exception:
        proc.kill()
        proc.wait()
        if dst.exists():
            dst.unlink(missing_ok=True)
        raise

    if proc.returncode != 0:
        if dst.exists():
            dst.unlink(missing_ok=True)
        err_msg = (stderr_text or "").strip() or f"ffmpeg exited with code {proc.returncode}"
        raise RuntimeError(err_msg)


def convert_media(
    src: Path,
    dst: Path,
    target_format: str,
    on_progress: Callable[[float], None] | None = None,
) -> None:
    """Convert an audio or video file using ffmpeg."""
    codec_args = FFMPEG_ARGS.get(target_format)
    if codec_args is None:
        raise ValueError(f"Unsupported media target format: {target_format}")
    run_ffmpeg_command(src, dst, codec_args, on_progress=on_progress)


def send_notification(title: str, body: str, urgency: str = "normal") -> None:
    """Send a desktop notification via notify-send if available."""
    notify_send = shutil.which("notify-send")
    if not notify_send:
        return
    try:
        subprocess.run(
            [
                notify_send,
                "-a", "TangerineLinux",
                "-u", urgency,
                title,
                body,
            ],
            check=False,
        )
    except OSError:
        pass


class ConversionSignals(QObject):
    """Qt signals emitted by ConversionWorker."""

    progress = pyqtSignal(int)
    finished = pyqtSignal(int, int, list)


class ConversionWorker(QRunnable):
    """QRunnable executing a batch of file conversions in QThreadPool."""

    def __init__(
        self,
        files: Sequence[Path],
        category: MediaCategory,
        target_format: str,
        skipped_count: int = 0,
    ) -> None:
        super().__init__()
        self.files = list(files)
        self.category = category
        self.target_format = target_format
        self.skipped_count = skipped_count
        self.signals = ConversionSignals()

    def run(self) -> None:
        total = len(self.files)
        if total == 0:
            self.signals.progress.emit(100)
            self.signals.finished.emit(0, 0, [])
            return

        reserved: set[Path] = set()
        outputs: list[Path] = []
        errors: list[str] = []
        succeeded = 0
        failed = 0

        self.signals.progress.emit(0)

        if self.category in (MediaCategory.AUDIO, MediaCategory.VIDEO) and find_ffmpeg() is None:
            msg = f"FFmpeg is required for audio/video tasks. Install it with: {install_command_for(['ffmpeg'])}"
            errors.append(msg)
            self.signals.progress.emit(100)
            self._notify_result(0, total, outputs, errors)
            self.signals.finished.emit(0, total, errors)
            return

        for idx, src in enumerate(self.files):
            dst = build_output_path(src, self.target_format, reserved=reserved)
            try:
                if self.category == MediaCategory.IMAGE:
                    convert_image(src, dst, self.target_format)
                    pct = int(((idx + 1) / total) * 100)
                    self.signals.progress.emit(pct)
                else:
                    def _report(file_ratio: float, index: int = idx) -> None:
                        overall = ((index + file_ratio) / total) * 100.0
                        self.signals.progress.emit(min(99, int(overall)))

                    convert_media(src, dst, self.target_format, on_progress=_report)
                    pct = int(((idx + 1) / total) * 100)
                    self.signals.progress.emit(pct)

                succeeded += 1
                outputs.append(dst)
            except Exception as exc:
                failed += 1
                errors.append(f"{src.name}: {exc}")
                pct = int(((idx + 1) / total) * 100)
                self.signals.progress.emit(pct)

        self._notify_result(succeeded, failed, outputs, errors)
        self.signals.finished.emit(succeeded, failed, errors)

    def _notify_result(
        self,
        succeeded: int,
        failed: int,
        outputs: list[Path],
        errors: list[str],
    ) -> None:
        skip_note = (
            f" (skipped {self.skipped_count} unsupported)"
            if self.skipped_count > 0
            else ""
        )
        if failed == 0 and succeeded == 1:
            send_notification(
                "Conversion complete",
                f"Saved {outputs[0].name} ({self.target_format}){skip_note}",
            )
        elif failed == 0:
            send_notification(
                "Batch conversion complete",
                f"Converted {succeeded} files to {self.target_format}{skip_note}",
            )
        elif succeeded > 0:
            send_notification(
                "Conversion partially complete",
                f"{succeeded} succeeded, {failed} failed ({errors[0]}){skip_note}",
                urgency="normal",
            )
        else:
            send_notification(
                "Conversion failed",
                errors[0] if errors else "Unknown conversion error",
                urgency="critical",
            )
