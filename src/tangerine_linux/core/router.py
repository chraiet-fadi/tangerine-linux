"""Context-aware file extension routing for TangerineLinux."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence


class MediaCategory(str, Enum):
    """Supported media categories."""

    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    PDF = "pdf"


class WheelMode(str, Enum):
    """Supported radial wheel operating modes."""

    CONVERT = "CONVERT"
    ACTIONS = "ACTIONS"


IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"})
AUDIO_EXTENSIONS: frozenset[str] = frozenset({".wav", ".mp3", ".flac", ".ogg", ".m4a", ".opus"})
VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mkv", ".mov", ".avi", ".webm"})
PDF_EXTENSIONS: frozenset[str] = frozenset({".pdf"})

CATEGORY_EXTENSIONS: dict[MediaCategory, frozenset[str]] = {
    MediaCategory.IMAGE: IMAGE_EXTENSIONS,
    MediaCategory.AUDIO: AUDIO_EXTENSIONS,
    MediaCategory.VIDEO: VIDEO_EXTENSIONS,
    MediaCategory.PDF: PDF_EXTENSIONS,
}

CONVERT_SLICES: dict[MediaCategory, tuple[str, ...]] = {
    MediaCategory.IMAGE: ("WEBP", "JPG", "PNG", "AVIF", "ICO", "TIFF"),
    MediaCategory.AUDIO: ("MP3", "FLAC", "OGG", "M4A", "WAV", "OPUS"),
    MediaCategory.VIDEO: ("MP4", "GIF", "WEBM", "MP3 (Audio)", "MKV", "MOV"),
}

ACTIONS_SLICES: dict[MediaCategory, tuple[str, ...]] = {
    MediaCategory.IMAGE: ("Remove BG", "OCR Text", "Compress", "To PDF", "Collage", "Strip EXIF"),
    MediaCategory.AUDIO: ("Compress 128k", "Normalize", "Trim Silence", "Mono Mix", "Extract Info", "Fade In/Out"),
    MediaCategory.VIDEO: ("Compress H.264", "720p Resize", "Mute Audio", "First Frame JPG", "Speed 2x", "Strip Metadata"),
    MediaCategory.PDF: ("Merge PDFs", "Split Pages", "Extract Text", "Compress PDF", "Rotate 90°", "Metadata Info"),
}


@dataclass(frozen=True)
class RouteResult:
    """Routing decision for a set of dragged/dropped files."""

    category: MediaCategory
    mode: WheelMode
    slices: tuple[str, ...]
    files: tuple[Path, ...]
    skipped_files: tuple[Path, ...] = ()


def detect_file_category(path: str | Path) -> MediaCategory | None:
    """Return the MediaCategory for a single file path based on its extension."""
    file_path = Path(path)
    if file_path.exists() and file_path.is_dir():
        return None
    ext = file_path.suffix.lower()
    for category, extensions in CATEGORY_EXTENSIONS.items():
        if ext in extensions:
            return category
    return None


def supports_mode(category: MediaCategory, mode: WheelMode) -> bool:
    """Return True if category supports the given wheel mode."""
    if mode == WheelMode.CONVERT:
        return category in CONVERT_SLICES
    return category in ACTIONS_SLICES


def default_mode_for_category(category: MediaCategory) -> WheelMode:
    """Return the default WheelMode for a detected media category."""
    if category == MediaCategory.PDF:
        return WheelMode.ACTIONS
    return WheelMode.CONVERT


def get_convert_slices(category: MediaCategory) -> tuple[str, ...]:
    """Return the 6 target format labels for Convert Mode."""
    return CONVERT_SLICES[category]


def get_action_slices(category: MediaCategory) -> tuple[str, ...]:
    """Return the 6 quick-action labels for Actions Mode."""
    return ACTIONS_SLICES[category]


def get_slices(category: MediaCategory, mode: WheelMode) -> tuple[str, ...]:
    """Return the 6 slice labels for a category and mode."""
    if mode == WheelMode.ACTIONS:
        return ACTIONS_SLICES[category]
    return CONVERT_SLICES.get(category, ACTIONS_SLICES[category])


def route_files(
    file_paths: Sequence[str | Path],
    mode: WheelMode | str | None = None,
) -> RouteResult | None:
    """Inspect paths and return the 6 slices for the requested (or default) mode.

    Uses the category of the first supported file and retains all files
    belonging to that same category. Defaults to ACTIONS for PDF files
    and CONVERT for images/audio/video. Returns None if no supported files exist.
    """
    primary_category: MediaCategory | None = None
    categorized: list[tuple[Path, MediaCategory | None]] = []

    for raw_path in file_paths:
        path = Path(raw_path)
        category = detect_file_category(path)
        if category is not None and primary_category is None:
            primary_category = category
        categorized.append((path, category))

    if primary_category is None:
        return None

    if mode is None:
        resolved_mode = default_mode_for_category(primary_category)
    else:
        resolved_mode = mode if isinstance(mode, WheelMode) else WheelMode(str(mode).upper())
        if not supports_mode(primary_category, resolved_mode):
            resolved_mode = WheelMode.ACTIONS

    matching_files = tuple(path for path, cat in categorized if cat == primary_category)
    skipped_files = tuple(path for path, cat in categorized if cat != primary_category)
    return RouteResult(
        category=primary_category,
        mode=resolved_mode,
        slices=get_slices(primary_category, resolved_mode),
        files=matching_files,
        skipped_files=skipped_files,
    )
