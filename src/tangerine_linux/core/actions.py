"""Asynchronous Quick Actions engine for TangerineLinux (Pillow, FFmpeg, pypdf)."""

from __future__ import annotations

import json
import multiprocessing
import queue as py_queue
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Sequence

from PIL import Image, ImageOps
from PyQt6.QtCore import QRunnable, pyqtSignal

from tangerine_linux.core.converters import (
    ConversionSignals,
    probe_duration_seconds,
    run_ffmpeg_command,
    send_notification,
    unique_output_path,
)
from tangerine_linux.core.deps import find_ffmpeg, find_tesseract, install_command_for
from tangerine_linux.core.router import MediaCategory

AUDIO_CODEC_BY_EXT: dict[str, list[str]] = {
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    ".flac": ["-c:a", "flac"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "5"],
    ".m4a": ["-c:a", "aac", "-b:a", "192k"],
    ".wav": ["-c:a", "pcm_s16le"],
    ".opus": ["-c:a", "libopus", "-b:a", "128k"],
}

LOSSY_AUDIO_128K_BY_EXT: dict[str, tuple[str, list[str]]] = {
    ".mp3": (".mp3", ["-vn", "-c:a", "libmp3lame", "-b:a", "128k"]),
    ".m4a": (".m4a", ["-vn", "-c:a", "aac", "-b:a", "128k"]),
    ".ogg": (".ogg", ["-vn", "-c:a", "libvorbis", "-b:a", "128k"]),
    ".opus": (".opus", ["-vn", "-c:a", "libopus", "-b:a", "128k"]),
}


def _flatten_alpha(img: Image.Image) -> Image.Image:
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (
        img.mode == "P" and "transparency" in img.info
    )
    if not has_alpha:
        return img.convert("RGB")
    rgba = img.convert("RGBA")
    bg = Image.new("RGB", rgba.size, (255, 255, 255))
    bg.paste(rgba, mask=rgba.getchannel("A"))
    return bg


def _save_image_preserving_ext(img: Image.Image, dst: Path, quality: int = 90) -> None:
    ext = dst.suffix.lower()
    out: Image.Image | None = None
    try:
        if ext in (".jpg", ".jpeg"):
            out = img if img.mode == "L" else _flatten_alpha(img)
            out.save(dst, format="JPEG", quality=quality, optimize=True)
        elif ext == ".webp":
            out = img if img.mode in ("RGB", "RGBA", "L") else img.convert("RGBA")
            out.save(dst, format="WEBP", quality=quality, method=6)
        elif ext == ".png":
            out = img if img.mode in ("RGB", "RGBA", "L", "LA") else img.convert("RGBA")
            out.save(dst, format="PNG", optimize=True, compress_level=9)
        elif ext == ".tiff":
            out = img if img.mode in ("RGB", "RGBA", "L", "LA") else img.convert("RGBA")
            out.save(dst, format="TIFF", compression="tiff_deflate")
        elif ext == ".bmp":
            out = img if img.mode in ("RGB", "L") else _flatten_alpha(img)
            out.save(dst, format="BMP")
        else:
            out = _flatten_alpha(img)
            out.save(dst)
    finally:
        if out is not None and out is not img:
            out.close()


# --- Image Actions ---


def _action_image_compress(src: Path, reserved: set[Path]) -> Path:
    ext = src.suffix.lower()
    out_ext = ".png" if ext == ".bmp" else ext
    dst = unique_output_path(src.parent, f"{src.stem}_compressed", out_ext, reserved=reserved)
    with Image.open(src) as opened:
        img = ImageOps.exif_transpose(opened)
        try:
            _save_image_preserving_ext(img, dst, quality=72)
        finally:
            if img is not opened:
                img.close()
    return dst


def _action_image_strip_exif(src: Path, reserved: set[Path]) -> Path:
    dst = unique_output_path(src.parent, f"{src.stem}_no_exif", src.suffix.lower(), reserved=reserved)
    with Image.open(src) as opened:
        img = ImageOps.exif_transpose(opened)
        try:
            with Image.new(img.mode, img.size) as clean:
                clean.paste(img)
                _save_image_preserving_ext(clean, dst, quality=92)
        finally:
            if img is not opened:
                img.close()
    return dst


def _action_image_resize_50(src: Path, reserved: set[Path]) -> Path:
    dst = unique_output_path(src.parent, f"{src.stem}_50pct", src.suffix.lower(), reserved=reserved)
    with Image.open(src) as opened:
        img = ImageOps.exif_transpose(opened)
        new_size = (max(1, img.width // 2), max(1, img.height // 2))
        try:
            with img.resize(new_size, Image.Resampling.LANCZOS) as resized:
                _save_image_preserving_ext(resized, dst, quality=90)
        finally:
            if img is not opened:
                img.close()
    return dst


def _action_image_grayscale(src: Path, reserved: set[Path]) -> Path:
    ext = src.suffix.lower()
    dst = unique_output_path(src.parent, f"{src.stem}_gray", ext, reserved=reserved)
    with Image.open(src) as opened:
        img = ImageOps.exif_transpose(opened)
        try:
            has_alpha = img.mode in ("RGBA", "LA", "PA") or (
                img.mode == "P" and "transparency" in img.info
            )
            if has_alpha and ext in (".png", ".webp", ".tiff"):
                with img.convert("RGBA") as rgba:
                    with ImageOps.grayscale(rgba).convert("LA") as gray:
                        gray.putalpha(rgba.getchannel("A"))
                        _save_image_preserving_ext(gray, dst, quality=90)
            else:
                with ImageOps.grayscale(img) as gray:
                    _save_image_preserving_ext(gray, dst, quality=90)
        finally:
            if img is not opened:
                img.close()
    return dst


def _action_images_to_pdf(files: Sequence[Path], reserved: set[Path]) -> Path:
    stem = files[0].stem if len(files) == 1 else f"{files[0].stem}_combined"
    dst = unique_output_path(files[0].parent, stem, ".pdf", reserved=reserved)
    rgb_pages: list[Image.Image] = []
    try:
        for path in files:
            with Image.open(path) as opened:
                transposed = ImageOps.exif_transpose(opened)
                try:
                    rgb_pages.append(_flatten_alpha(transposed))
                finally:
                    if transposed is not opened:
                        transposed.close()
        rgb_pages[0].save(
            dst,
            format="PDF",
            save_all=True,
            append_images=rgb_pages[1:],
            resolution=150.0,
        )
    finally:
        for page in rgb_pages:
            page.close()
    return dst


def _action_images_collage(files: Sequence[Path], reserved: set[Path]) -> Path:
    selected = list(files[:4])
    dst = unique_output_path(selected[0].parent, f"{selected[0].stem}_collage", ".png", reserved=reserved)

    gutter = 24
    bg_color = (245, 245, 247)
    count = len(selected)

    if count == 1:
        cols, rows, cell = 1, 1, 900
    elif count == 2:
        cols, rows, cell = 2, 1, 800
    else:
        cols, rows, cell = 2, 2, 700

    canvas_w = cols * cell + (cols + 1) * gutter
    canvas_h = rows * cell + (rows + 1) * gutter
    with Image.new("RGB", (canvas_w, canvas_h), bg_color) as canvas:
        for idx, path in enumerate(selected):
            r, c = divmod(idx, cols)
            x = gutter + c * (cell + gutter)
            y = gutter + r * (cell + gutter)
            with Image.open(path) as opened:
                transposed = ImageOps.exif_transpose(opened)
                try:
                    with _flatten_alpha(transposed) as flat:
                        with ImageOps.fit(flat, (cell, cell), Image.Resampling.LANCZOS) as fitted:
                            canvas.paste(fitted, (x, y))
                finally:
                    if transposed is not opened:
                        transposed.close()

        canvas.save(dst, format="PNG", optimize=True)
    return dst


# --- Audio Actions ---


def _audio_codec_for(src: Path) -> list[str]:
    return AUDIO_CODEC_BY_EXT.get(src.suffix.lower(), ["-c:a", "libmp3lame", "-q:a", "2"])


def _probe_audio_stream_exists(src: Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return True
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index",
                "-of", "csv=p=0",
                str(src.resolve()),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return bool(proc.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return True


def _probe_media_summary(src: Path) -> str:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe executable not found on PATH.")
    proc = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-show_format",
            "-show_streams",
            "-of", "json",
            str(src.resolve()),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffprobe failed")
    data = json.loads(proc.stdout or "{}")
    fmt = data.get("format", {})
    lines = [
        f"File: {src.name}",
        f"Format: {fmt.get('format_long_name') or fmt.get('format_name', 'unknown')}",
        f"Duration (s): {fmt.get('duration', 'unknown')}",
        f"Size (bytes): {fmt.get('size', 'unknown')}",
        f"Bitrate (bps): {fmt.get('bit_rate', 'unknown')}",
    ]
    tags = fmt.get("tags") or {}
    if tags:
        lines.append("Tags:")
        for k, v in tags.items():
            lines.append(f"  {k}: {v}")
    for idx, stream in enumerate(data.get("streams", [])):
        lines.append(
            f"Stream #{idx} [{stream.get('codec_type', '?')}]: "
            f"codec={stream.get('codec_name', '?')}, "
            f"sample_rate={stream.get('sample_rate', '-')}, "
            f"channels={stream.get('channels', '-')}, "
            f"bit_rate={stream.get('bit_rate', '-')}"
        )
    return "\n".join(lines) + "\n"


def _run_audio_action(
    src: Path,
    action: str,
    reserved: set[Path],
    on_progress: Callable[[float], None] | None,
) -> Path:
    ext = src.suffix.lower()
    if action == "Compress 128k":
        out_ext, args = LOSSY_AUDIO_128K_BY_EXT.get(
            ext, (".mp3", ["-vn", "-c:a", "libmp3lame", "-b:a", "128k"])
        )
        dst = unique_output_path(src.parent, f"{src.stem}_128k", out_ext, reserved=reserved)
        run_ffmpeg_command(src, dst, args, on_progress=on_progress)
        return dst

    if action == "Extract Info":
        dst = unique_output_path(src.parent, f"{src.stem}_info", ".txt", reserved=reserved)
        dst.write_text(_probe_media_summary(src), encoding="utf-8")
        if on_progress is not None:
            on_progress(1.0)
        return dst

    codec_args = _audio_codec_for(src)
    if action == "Normalize":
        dst = unique_output_path(src.parent, f"{src.stem}_normalized", ext, reserved=reserved)
        args = ["-vn", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", *codec_args]
    elif action == "Trim Silence":
        dst = unique_output_path(src.parent, f"{src.stem}_trimmed", ext, reserved=reserved)
        af = (
            "silenceremove=start_periods=1:start_duration=0.2:start_threshold=-45dB:detection=peak,"
            "areverse,"
            "silenceremove=start_periods=1:start_duration=0.2:start_threshold=-45dB:detection=peak,"
            "areverse"
        )
        args = ["-vn", "-af", af, *codec_args]
    elif action == "Mono Mix":
        dst = unique_output_path(src.parent, f"{src.stem}_mono", ext, reserved=reserved)
        args = ["-vn", "-ac", "1", *codec_args]
    elif action == "Fade In/Out":
        dst = unique_output_path(src.parent, f"{src.stem}_faded", ext, reserved=reserved)
        duration = probe_duration_seconds(src)
        if duration and duration > 0:
            fade_d = min(1.5, duration / 3.0)
            out_start = max(0.0, duration - fade_d)
            af = f"afade=t=in:st=0:d={fade_d:.3f},afade=t=out:st={out_start:.3f}:d={fade_d:.3f}"
        else:
            af = "afade=t=in:st=0:d=1.5"
        args = ["-vn", "-af", af, *codec_args]
    else:
        raise ValueError(f"Unsupported audio action: {action}")

    run_ffmpeg_command(src, dst, args, on_progress=on_progress)
    return dst


# --- Video Actions ---


def _run_video_action(
    src: Path,
    action: str,
    reserved: set[Path],
    on_progress: Callable[[float], None] | None,
) -> Path:
    ext = src.suffix.lower()
    if action == "Compress H.264":
        dst = unique_output_path(src.parent, f"{src.stem}_compressed", ".mp4", reserved=reserved)
        args = [
            "-c:v", "libx264", "-preset", "medium", "-crf", "28",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
        ]
    elif action == "720p Resize":
        out_ext = ext if ext in (".mp4", ".mkv", ".mov") else ".mp4"
        dst = unique_output_path(src.parent, f"{src.stem}_720p", out_ext, reserved=reserved)
        args = [
            "-vf", "scale=-2:'min(720,ih)'",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        ]
    elif action == "Mute Audio":
        dst = unique_output_path(src.parent, f"{src.stem}_muted", ext, reserved=reserved)
        args = ["-c:v", "copy", "-an"]
    elif action == "First Frame JPG":
        dst = unique_output_path(src.parent, f"{src.stem}_frame", ".jpg", reserved=reserved)
        args = ["-vframes", "1", "-q:v", "2"]
    elif action == "Speed 2x":
        out_ext = ext if ext in (".mp4", ".mkv", ".mov") else ".mp4"
        dst = unique_output_path(src.parent, f"{src.stem}_2x", out_ext, reserved=reserved)
        args = [
            "-vf", "setpts=0.5*PTS",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p",
        ]
        if _probe_audio_stream_exists(src):
            args.extend(["-af", "atempo=2.0", "-c:a", "aac", "-b:a", "192k"])
        else:
            args.append("-an")
    elif action == "Strip Metadata":
        dst = unique_output_path(src.parent, f"{src.stem}_clean", ext, reserved=reserved)
        args = ["-map_metadata", "-1", "-c", "copy"]
    else:
        raise ValueError(f"Unsupported video action: {action}")

    run_ffmpeg_command(src, dst, args, on_progress=on_progress)
    return dst


# --- PDF Actions (pypdf lazy-imported inside functions for zero idle RAM) ---


def _action_pdf_merge(files: Sequence[Path], reserved: set[Path]) -> Path:
    from pypdf import PdfReader, PdfWriter

    dst = unique_output_path(files[0].parent, "merged_output", ".pdf", reserved=reserved)
    writer = PdfWriter()
    for path in files:
        reader = PdfReader(str(path))
        for page in reader.pages:
            writer.add_page(page)
    with dst.open("wb") as fh:
        writer.write(fh)
    return dst


def _action_pdf_split(src: Path, reserved: set[Path]) -> list[Path]:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(src))
    outputs: list[Path] = []
    for idx, page in enumerate(reader.pages, start=1):
        dst = unique_output_path(src.parent, f"{src.stem}_page_{idx:03d}", ".pdf", reserved=reserved)
        writer = PdfWriter()
        writer.add_page(page)
        with dst.open("wb") as fh:
            writer.write(fh)
        outputs.append(dst)
    return outputs


def _action_pdf_extract_text(src: Path, reserved: set[Path]) -> Path:
    from pypdf import PdfReader

    dst = unique_output_path(src.parent, f"{src.stem}_text", ".txt", reserved=reserved)
    reader = PdfReader(str(src))
    chunks: list[str] = []
    for idx, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        chunks.append(f"--- Page {idx} ---\n{text}\n")
    dst.write_text("\n".join(chunks), encoding="utf-8")
    return dst


def _action_pdf_compress(src: Path, reserved: set[Path]) -> Path:
    from pypdf import PdfReader, PdfWriter

    dst = unique_output_path(src.parent, f"{src.stem}_compressed", ".pdf", reserved=reserved)
    reader = PdfReader(str(src))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    for page in writer.pages:
        page.compress_content_streams()
    writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    with dst.open("wb") as fh:
        writer.write(fh)
    return dst


def _action_pdf_rotate_90(src: Path, reserved: set[Path]) -> Path:
    from pypdf import PdfReader, PdfWriter

    dst = unique_output_path(src.parent, f"{src.stem}_rotated", ".pdf", reserved=reserved)
    reader = PdfReader(str(src))
    writer = PdfWriter()
    for page in reader.pages:
        page.rotate(90)
        writer.add_page(page)
    with dst.open("wb") as fh:
        writer.write(fh)
    return dst


def _action_pdf_metadata_info(src: Path, reserved: set[Path]) -> Path:
    from pypdf import PdfReader

    dst = unique_output_path(src.parent, f"{src.stem}_info", ".txt", reserved=reserved)
    reader = PdfReader(str(src))
    lines = [
        f"File: {src.name}",
        f"Pages: {len(reader.pages)}",
    ]
    if reader.pages:
        box = reader.pages[0].mediabox
        lines.append(f"Page 1 Size (pt): {float(box.width):.1f} x {float(box.height):.1f}")
    meta = reader.metadata or {}
    if meta:
        lines.append("Metadata:")
        for key, value in meta.items():
            lines.append(f"  {key}: {value}")
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dst


# --- Ephemeral AI Child Processes (Zero-Idle-RAM via "spawn") ---


def _ephemeral_remove_bg_child(tasks: list[tuple[str, str]], event_q: Any) -> None:
    """Run rembg u2netp background removal inside an isolated spawned process."""
    try:
        from PIL import Image as PilImage, ImageOps as PilImageOps
        from rembg import new_session, remove

        session = new_session("u2netp")
        total = len(tasks)
        for idx, (src_str, dst_str) in enumerate(tasks):
            try:
                with PilImage.open(src_str) as opened:
                    img = PilImageOps.exif_transpose(opened)
                    out_img = None
                    try:
                        out_img = remove(img, session=session)
                        out_img.save(dst_str, format="PNG", optimize=True)
                    finally:
                        if out_img is not None and out_img is not img:
                            out_img.close()
                        if img is not opened:
                            img.close()
                event_q.put(("ok", dst_str, idx + 1, total, ""))
            except Exception as exc:
                event_q.put(("err", f"{Path(src_str).name}: {exc}", idx + 1, total, ""))
    except Exception as exc:
        event_q.put(("fatal", str(exc), len(tasks), len(tasks), ""))


def _ephemeral_ocr_child(tasks: list[tuple[str, str]], event_q: Any) -> None:
    """Run pytesseract OCR extraction inside an isolated spawned process."""
    try:
        import pytesseract
        from PIL import Image as PilImage, ImageOps as PilImageOps

        if shutil.which("tesseract") is None:
            event_q.put((
                "fatal",
                f"Tesseract OCR is required. Install it with: {install_command_for(['tesseract'])}",
                len(tasks),
                len(tasks),
                "",
            ))
            return

        total = len(tasks)
        for idx, (src_str, dst_str) in enumerate(tasks):
            try:
                with PilImage.open(src_str) as opened:
                    img = PilImageOps.exif_transpose(opened)
                    try:
                        text = pytesseract.image_to_string(img).strip()
                    finally:
                        if img is not opened:
                            img.close()
                Path(dst_str).write_text(text + ("\n" if text else ""), encoding="utf-8")
                event_q.put(("ok", dst_str, idx + 1, total, text))
            except Exception as exc:
                event_q.put(("err", f"{Path(src_str).name}: {exc}", idx + 1, total, ""))
    except Exception as exc:
        event_q.put(("fatal", str(exc), len(tasks), len(tasks), ""))


# --- Async Worker ---


class ActionSignals(ConversionSignals):
    """Signals emitted by ActionWorker, including extracted OCR clipboard text."""

    clipboard_text = pyqtSignal(str)


class ActionWorker(QRunnable):
    """QRunnable executing Quick Actions asynchronously in QThreadPool."""

    def __init__(
        self,
        files: Sequence[Path],
        category: MediaCategory,
        action_name: str,
        skipped_count: int = 0,
    ) -> None:
        super().__init__()
        self.files = list(files)
        self.category = category
        self.action_name = action_name
        self.skipped_count = skipped_count
        self.signals = ActionSignals()

    def run(self) -> None:
        if not self.files:
            self.signals.progress.emit(100)
            self.signals.finished.emit(0, 0, [])
            return

        reserved: set[Path] = set()
        outputs: list[Path] = []
        errors: list[str] = []
        succeeded = 0
        failed = 0
        copied_to_clipboard = False

        self.signals.progress.emit(0)

        try:
            if self.category in (MediaCategory.AUDIO, MediaCategory.VIDEO) and find_ffmpeg() is None:
                raise RuntimeError(
                    f"FFmpeg is required for audio/video tasks. Install it with: {install_command_for(['ffmpeg'])}"
                )

            if self.category == MediaCategory.IMAGE and self.action_name == "OCR Text" and find_tesseract() is None:
                raise RuntimeError(
                    f"Tesseract OCR is required. Install it with: {install_command_for(['tesseract'])}"
                )

            if self.category == MediaCategory.IMAGE and self.action_name in ("Remove BG", "OCR Text"):
                succeeded, failed, outputs, errors, ocr_chunks = self._run_ephemeral_ai_action(reserved)
                if ocr_chunks:
                    combined_text = "\n\n".join(chunk for chunk in ocr_chunks if chunk).strip()
                    if combined_text:
                        self.signals.clipboard_text.emit(combined_text)
                        copied_to_clipboard = True
            elif self.category == MediaCategory.IMAGE and self.action_name in ("To PDF", "Collage"):
                if self.action_name == "To PDF":
                    outputs.append(_action_images_to_pdf(self.files, reserved))
                else:
                    outputs.append(_action_images_collage(self.files, reserved))
                succeeded = 1
                self.signals.progress.emit(100)
            elif self.category == MediaCategory.PDF and self.action_name == "Merge PDFs":
                outputs.append(_action_pdf_merge(self.files, reserved))
                succeeded = 1
                self.signals.progress.emit(100)
            else:
                total = len(self.files)
                for idx, src in enumerate(self.files):
                    try:
                        def _report(file_ratio: float, index: int = idx) -> None:
                            overall = ((index + file_ratio) / total) * 100.0
                            self.signals.progress.emit(min(99, int(overall)))

                        created = self._run_per_file(src, reserved, _report)
                        outputs.extend(created)
                        succeeded += 1
                    except Exception as exc:
                        failed += 1
                        errors.append(f"{src.name}: {exc}")
                    self.signals.progress.emit(int(((idx + 1) / total) * 100))
        except Exception as exc:
            failed += 1
            errors.append(str(exc))
            self.signals.progress.emit(100)

        self._notify_result(succeeded, failed, outputs, errors, copied_to_clipboard=copied_to_clipboard)
        self.signals.finished.emit(succeeded, failed, errors)

    def _run_ephemeral_ai_action(
        self,
        reserved: set[Path],
    ) -> tuple[int, int, list[Path], list[str], list[str]]:
        is_remove_bg = self.action_name == "Remove BG"
        tasks: list[tuple[str, str]] = []
        for src in self.files:
            if is_remove_bg:
                dst = unique_output_path(src.parent, f"{src.stem}_nobg", ".png", reserved=reserved)
            else:
                dst = unique_output_path(src.parent, f"{src.stem}_ocr", ".txt", reserved=reserved)
            tasks.append((str(src.resolve()), str(dst.resolve())))

        ctx = multiprocessing.get_context("spawn")
        event_q = ctx.Queue()
        target = _ephemeral_remove_bg_child if is_remove_bg else _ephemeral_ocr_child
        proc = ctx.Process(target=target, args=(tasks, event_q), daemon=False)

        outputs: list[Path] = []
        errors: list[str] = []
        ocr_chunks: list[str] = []
        succeeded = 0
        failed = 0

        def _handle_event(item: tuple[str, str, int, int, str]) -> None:
            nonlocal succeeded, failed
            kind, payload, done_count, total, extra_text = item
            if kind == "ok":
                succeeded += 1
                outputs.append(Path(payload))
                if extra_text:
                    ocr_chunks.append(extra_text)
            elif kind == "err":
                failed += 1
                errors.append(payload)
            elif kind == "fatal":
                failed = len(tasks)
                errors.append(payload)

            if total > 0:
                self.signals.progress.emit(min(99, max(5, int((done_count / total) * 100))))

        self.signals.progress.emit(5)
        proc.start()

        try:
            while proc.is_alive():
                try:
                    _handle_event(event_q.get(timeout=0.1))
                except py_queue.Empty:
                    continue
        finally:
            proc.join(timeout=5)
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=2)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=1)

            while True:
                try:
                    _handle_event(event_q.get_nowait())
                except py_queue.Empty:
                    break

            event_q.close()
            event_q.join_thread()

        if proc.exitcode not in (0, None) and succeeded == 0 and not errors:
            failed = len(tasks)
            errors.append(f"AI worker process exited with code {proc.exitcode}")

        self.signals.progress.emit(100)
        return succeeded, failed, outputs, errors, ocr_chunks

    def _run_per_file(
        self,
        src: Path,
        reserved: set[Path],
        on_progress: Callable[[float], None],
    ) -> list[Path]:
        if self.category == MediaCategory.IMAGE:
            handlers = {
                "Compress": _action_image_compress,
                "Strip EXIF": _action_image_strip_exif,
                "Resize 50%": _action_image_resize_50,
                "Grayscale": _action_image_grayscale,
            }
            handler = handlers.get(self.action_name)
            if handler is None:
                raise ValueError(f"Unsupported image action: {self.action_name}")
            return [handler(src, reserved)]

        if self.category == MediaCategory.AUDIO:
            return [_run_audio_action(src, self.action_name, reserved, on_progress)]

        if self.category == MediaCategory.VIDEO:
            return [_run_video_action(src, self.action_name, reserved, on_progress)]

        if self.category == MediaCategory.PDF:
            if self.action_name == "Split Pages":
                return _action_pdf_split(src, reserved)
            pdf_handlers = {
                "Extract Text": _action_pdf_extract_text,
                "Compress PDF": _action_pdf_compress,
                "Rotate 90°": _action_pdf_rotate_90,
                "Metadata Info": _action_pdf_metadata_info,
            }
            handler = pdf_handlers.get(self.action_name)
            if handler is None:
                raise ValueError(f"Unsupported PDF action: {self.action_name}")
            return [handler(src, reserved)]

        raise ValueError(f"Unsupported category: {self.category}")

    def _notify_result(
        self,
        succeeded: int,
        failed: int,
        outputs: list[Path],
        errors: list[str],
        copied_to_clipboard: bool = False,
    ) -> None:
        clip_suffix = " & copied to clipboard" if copied_to_clipboard else ""
        skip_note = (
            f" (skipped {self.skipped_count} unsupported)"
            if self.skipped_count > 0
            else ""
        )
        if failed == 0 and outputs:
            if len(outputs) == 1:
                send_notification(
                    f"{self.action_name} complete",
                    f"Saved {outputs[0].name}{clip_suffix}{skip_note}",
                )
            else:
                send_notification(
                    f"{self.action_name} complete",
                    f"Created {len(outputs)} output files{clip_suffix}{skip_note}",
                )
        elif succeeded > 0:
            send_notification(
                f"{self.action_name} partially complete",
                f"{succeeded} succeeded, {failed} failed ({errors[0]}){skip_note}",
                urgency="normal",
            )
        else:
            send_notification(
                f"{self.action_name} failed",
                errors[0] if errors else "Unknown action error",
                urgency="critical",
            )
