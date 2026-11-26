"""Glassify frosted-acrylic RadialWheel and citrus glass-drop FloatingOrb UI for TangerineLinux."""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QThreadPool,
    QTimer,
    QVariantAnimation,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QEnterEvent,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QHideEvent,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QRadialGradient,
    QShowEvent,
)
from PyQt6.QtWidgets import QApplication, QWidget

from tangerine_linux.core.actions import ActionWorker
from tangerine_linux.core.converters import ConversionWorker
from tangerine_linux.core.router import (
    MediaCategory,
    RouteResult,
    WheelMode,
    get_slices,
    route_files,
    supports_mode,
)

ORB_WINDOW_SIZE = 56
ORB_DRAW_DIAMETER = 42
WHEEL_SIZE = 320
CHASSIS_DIAMETER = 292
CHASSIS_RADIUS = CHASSIS_DIAMETER / 2.0  # 146.0
SCREEN_RIGHT_MARGIN = 24

TANGERINE_RING_HEX = "#FF5E00"
ACTIONS_BADGE_HEX = "#7C3AED"
TANGERINE_TOP = QColor(255, 123, 28, 240)     # #FF7B1C
TANGERINE_BOTTOM = QColor(242, 78, 0, 245)    # #F24E00

SLICE_OUTER_RADIUS = 132.0
SLICE_INNER_RADIUS = 64.0
HUB_RADIUS = 54.0
SLICE_GAP_DEG = 4.0
SLICE_SWEEP_DEG = 60.0 - SLICE_GAP_DEG  # 56.0 deg
PETAL_CORNER_RADIUS = 6.0
HOVER_POP_OUT_PX = 5.0
SLICE_COUNT = 6


def _extract_local_paths(event: QDragEnterEvent | QDropEvent) -> list[Path]:
    """Extract local filesystem paths from a Qt drag/drop event."""
    mime = event.mimeData()
    if mime is None or not mime.hasUrls():
        return []
    paths: list[Path] = []
    for url in mime.urls():
        if url.isLocalFile():
            local = url.toLocalFile()
            if local:
                paths.append(Path(local))
    return paths


def _format_file_badge(route: RouteResult) -> str:
    """Return a concise file count/type badge such as '1 PNG' or '2 JPGs'."""
    count = len(route.files)
    exts = {p.suffix.lstrip(".").upper() for p in route.files if p.suffix}
    if len(exts) == 1:
        ext = next(iter(exts))
        return f"{count} {ext}" if count == 1 else f"{count} {ext}s"

    names = {
        MediaCategory.IMAGE: ("Image", "Images"),
        MediaCategory.AUDIO: ("Audio", "Audio"),
        MediaCategory.VIDEO: ("Video", "Videos"),
        MediaCategory.PDF: ("PDF", "PDFs"),
    }
    singular, plural = names[route.category]
    return f"{count} {singular if count == 1 else plural}"


def _build_rounded_petal_path(
    cx: float,
    cy: float,
    r_in: float,
    r_out: float,
    mid_deg: float,
    sweep_deg: float,
    fillet: float = PETAL_CORNER_RADIUS,
) -> QPainterPath:
    """Build a smoothly rounded-corner annular petal using arcTo + quadTo fillets."""
    half_deg = sweep_deg / 2.0
    theta_start = mid_deg - half_deg
    theta_end = mid_deg + half_deg

    d_out = math.degrees(fillet / r_out)
    d_in = math.degrees(fillet / r_in)

    t_s_rad = math.radians(theta_start)
    t_e_rad = math.radians(theta_end)
    t_s_out_rad = math.radians(theta_start + d_out)
    t_s_in_rad = math.radians(theta_start + d_in)
    t_e_in_rad = math.radians(theta_end - d_in)

    def _pt(r: float, ang_rad: float) -> QPointF:
        return QPointF(cx + r * math.cos(ang_rad), cy - r * math.sin(ang_rad))

    outer_rect = QRectF(cx - r_out, cy - r_out, 2.0 * r_out, 2.0 * r_out)
    inner_rect = QRectF(cx - r_in, cy - r_in, 2.0 * r_in, 2.0 * r_in)

    path = QPainterPath()
    # Start on theta_start radial edge just below outer corner fillet
    path.moveTo(_pt(r_out - fillet, t_s_rad))
    # Outer-start corner fillet -> outer arc
    path.quadTo(_pt(r_out, t_s_rad), _pt(r_out, t_s_out_rad))
    path.arcTo(outer_rect, theta_start + d_out, sweep_deg - 2.0 * d_out)
    # Outer-end corner fillet -> theta_end radial edge
    path.quadTo(_pt(r_out, t_e_rad), _pt(r_out - fillet, t_e_rad))
    path.lineTo(_pt(r_in + fillet, t_e_rad))
    # Inner-end corner fillet -> inner arc
    path.quadTo(_pt(r_in, t_e_rad), _pt(r_in, t_e_in_rad))
    path.arcTo(inner_rect, theta_end - d_in, -(sweep_deg - 2.0 * d_in))
    # Inner-start corner fillet -> close along theta_start radial edge
    path.quadTo(_pt(r_in, t_s_rad), _pt(r_in + fillet, t_s_rad))
    path.closeSubpath()
    return path


def _draw_slice_icon(
    painter: QPainter,
    label: str,
    category: MediaCategory | None,
    center: QPointF,
    color: QColor,
) -> None:
    """Draw a crisp 16x16 monochrome stroke/vector glyph centered at `center`."""
    painter.save()
    painter.translate(center)
    pen = QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if label == "Remove BG":
        # Magic wand + sparkles
        painter.drawLine(QPointF(-5.5, 5.5), QPointF(2.5, -2.5))
        # 4-point main sparkle at wand tip
        spark = QPainterPath()
        spark.moveTo(3.5, -6.5)
        spark.lineTo(4.4, -4.4)
        spark.lineTo(6.5, -3.5)
        spark.lineTo(4.4, -2.6)
        spark.lineTo(3.5, -0.5)
        spark.lineTo(2.6, -2.6)
        spark.lineTo(0.5, -3.5)
        spark.lineTo(2.6, -4.4)
        spark.closeSubpath()
        painter.setBrush(color)
        painter.drawPath(spark)
        # Tiny secondary sparkle
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(-4.0, -4.5), QPointF(-4.0, -1.5))
        painter.drawLine(QPointF(-5.5, -3.0), QPointF(-2.5, -3.0))

    elif label == "OCR Text":
        # Corner scan brackets + text scan lines
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            painter.drawLine(QPointF(sx * 6.0, sy * 3.0), QPointF(sx * 6.0, sy * 5.5))
            painter.drawLine(QPointF(sx * 6.0, sy * 5.5), QPointF(sx * 3.2, sy * 5.5))
        painter.drawLine(QPointF(-3.5, -2.2), QPointF(3.5, -2.2))
        painter.drawLine(QPointF(-3.5, 0.2), QPointF(3.5, 0.2))
        painter.drawLine(QPointF(-3.5, 2.6), QPointF(1.5, 2.6))

    elif label in ("Compress", "Compress H.264", "Compress 128k", "Compress PDF"):
        painter.drawLine(QPointF(-6.5, 0.0), QPointF(-2.0, 0.0))
        painter.drawLine(QPointF(-4.2, -2.6), QPointF(-1.8, 0.0))
        painter.drawLine(QPointF(-4.2, 2.6), QPointF(-1.8, 0.0))
        painter.drawLine(QPointF(6.5, 0.0), QPointF(2.0, 0.0))
        painter.drawLine(QPointF(4.2, -2.6), QPointF(1.8, 0.0))
        painter.drawLine(QPointF(4.2, 2.6), QPointF(1.8, 0.0))
        painter.drawLine(QPointF(0.0, -5.8), QPointF(0.0, 5.8))

    elif label == "Collage":
        for dx, dy in ((-5.8, -5.8), (1.2, -5.8), (-5.8, 1.2), (1.2, 1.2)):
            painter.drawRoundedRect(QRectF(dx, dy, 4.6, 4.6), 1.0, 1.0)

    elif label == "To PDF":
        painter.drawRoundedRect(QRectF(-5.8, -6.2, 8.2, 10.2), 1.2, 1.2)
        back = QPainterPath()
        back.moveTo(-2.5, -6.2)
        back.lineTo(-2.5, -7.2)
        back.lineTo(5.2, -7.2)
        back.lineTo(5.2, 3.5)
        back.lineTo(2.4, 3.5)
        painter.drawPath(back)
        painter.drawLine(QPointF(-3.6, -1.8), QPointF(0.4, -1.8))
        painter.drawLine(QPointF(-3.6, 1.2), QPointF(0.4, 1.2))

    elif label == "Extract Text":
        page = QPainterPath()
        page.moveTo(-4.8, -6.5)
        page.lineTo(1.5, -6.5)
        page.lineTo(4.8, -3.2)
        page.lineTo(4.8, 6.5)
        page.lineTo(-4.8, 6.5)
        page.closeSubpath()
        painter.drawPath(page)
        painter.drawLine(QPointF(1.5, -6.5), QPointF(1.5, -3.2))
        painter.drawLine(QPointF(1.5, -3.2), QPointF(4.8, -3.2))
        painter.drawLine(QPointF(-2.4, 0.0), QPointF(2.4, 0.0))
        painter.drawLine(QPointF(-2.4, 3.0), QPointF(2.4, 3.0))

    elif label in ("Extract Info", "Metadata Info"):
        painter.drawEllipse(QRectF(-6.0, -6.0, 12.0, 12.0))
        painter.drawLine(QPointF(0.0, -0.4), QPointF(0.0, 3.4))
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(0.0, -3.0), 1.1, 1.1)

    elif label in ("Strip EXIF", "Strip Metadata"):
        shield = QPainterPath()
        shield.moveTo(0.0, -6.5)
        shield.lineTo(5.2, -4.4)
        shield.lineTo(5.2, 0.2)
        shield.quadTo(5.2, 4.4, 0.0, 6.5)
        shield.quadTo(-5.2, 4.4, -5.2, 0.2)
        shield.lineTo(-5.2, -4.4)
        shield.closeSubpath()
        painter.drawPath(shield)
        painter.drawLine(QPointF(-5.5, 4.8), QPointF(5.5, -5.2))

    elif label in ("Resize 50%", "720p Resize"):
        painter.drawLine(QPointF(-5.5, 5.5), QPointF(5.5, -5.5))
        painter.drawLine(QPointF(1.5, -5.5), QPointF(5.5, -5.5))
        painter.drawLine(QPointF(5.5, -5.5), QPointF(5.5, -1.5))
        painter.drawLine(QPointF(-5.5, 1.5), QPointF(-5.5, 5.5))
        painter.drawLine(QPointF(-5.5, 5.5), QPointF(-1.5, 5.5))

    elif label == "Grayscale":
        painter.drawEllipse(QRectF(-6.0, -6.0, 12.0, 12.0))
        half = QPainterPath()
        half.moveTo(0.0, -6.0)
        half.arcTo(QRectF(-6.0, -6.0, 12.0, 12.0), 90.0, 180.0)
        half.closeSubpath()
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(half)

    elif label in ("Trim Silence", "Split Pages"):
        painter.drawEllipse(QPointF(-3.8, 3.8), 2.0, 2.0)
        painter.drawEllipse(QPointF(3.8, 3.8), 2.0, 2.0)
        painter.drawLine(QPointF(-2.5, 2.2), QPointF(4.2, -5.5))
        painter.drawLine(QPointF(2.5, 2.2), QPointF(-4.2, -5.5))

    elif label == "Normalize":
        painter.drawLine(QPointF(-6.5, -2.5), QPointF(6.5, -2.5))
        for x in (-4.5, -1.5, 1.5, 4.5):
            painter.drawLine(QPointF(x, -2.5), QPointF(x, 5.0))

    elif label == "Mono Mix":
        painter.drawEllipse(QPointF(0.0, 0.0), 5.6, 5.6)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(0.0, 0.0), 2.0, 2.0)

    elif label == "Fade In/Out":
        env = QPainterPath()
        env.moveTo(-6.5, 4.5)
        env.lineTo(-2.4, -4.0)
        env.lineTo(2.4, -4.0)
        env.lineTo(6.5, 4.5)
        painter.drawPath(env)

    elif label == "Mute Audio":
        speaker = QPainterPath()
        speaker.moveTo(-5.8, -2.0)
        speaker.lineTo(-3.2, -2.0)
        speaker.lineTo(0.4, -5.0)
        speaker.lineTo(0.4, 5.0)
        speaker.lineTo(-3.2, 2.0)
        speaker.lineTo(-5.8, 2.0)
        speaker.closeSubpath()
        painter.drawPath(speaker)
        painter.drawLine(QPointF(2.6, -2.5), QPointF(6.2, 2.5))
        painter.drawLine(QPointF(6.2, -2.5), QPointF(2.6, 2.5))

    elif label == "First Frame JPG":
        painter.drawRoundedRect(QRectF(-6.5, -4.5, 13.0, 9.5), 2.0, 2.0)
        painter.drawEllipse(QPointF(0.0, 0.3), 2.5, 2.5)

    elif label == "Speed 2x":
        for offset in (-5.0, 0.5):
            tri = QPainterPath()
            tri.moveTo(offset, -4.2)
            tri.lineTo(offset + 4.5, 0.0)
            tri.lineTo(offset, 4.2)
            tri.closeSubpath()
            painter.drawPath(tri)

    elif label == "Merge PDFs":
        painter.drawRoundedRect(QRectF(-6.0, -6.0, 7.5, 9.0), 1.2, 1.2)
        painter.drawRoundedRect(QRectF(-1.5, -3.0, 7.5, 9.0), 1.2, 1.2)

    elif label == "Rotate 90°":
        painter.drawArc(QRectF(-5.5, -5.5, 11.0, 11.0), 45 * 16, 260 * 16)
        painter.drawLine(QPointF(3.8, -4.2), QPointF(5.8, -4.2))
        painter.drawLine(QPointF(3.8, -4.2), QPointF(3.8, -1.8))

    elif label == "WEBP":
        painter.drawEllipse(QRectF(-6.0, -6.0, 12.0, 12.0))
        painter.drawEllipse(QRectF(-2.8, -6.0, 5.6, 12.0))
        painter.drawLine(QPointF(-5.8, 0.0), QPointF(5.8, 0.0))

    elif label == "JPG":
        painter.drawRoundedRect(QRectF(-6.5, -5.0, 13.0, 10.0), 2.0, 2.0)
        painter.drawLine(QPointF(-4.8, 3.2), QPointF(-1.4, -0.4))
        painter.drawLine(QPointF(-1.4, -0.4), QPointF(1.2, 2.0))
        painter.drawLine(QPointF(1.2, 2.0), QPointF(3.2, 0.2))
        painter.drawLine(QPointF(3.2, 0.2), QPointF(5.0, 3.2))
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(3.0, -2.2), 1.1, 1.1)

    elif label == "PNG":
        painter.drawRoundedRect(QRectF(-6.0, -5.2, 9.5, 9.5), 1.6, 1.6)
        painter.drawRoundedRect(QRectF(-2.5, -2.2, 8.5, 8.2), 1.6, 1.6)

    elif label == "AVIF":
        diamond = QPainterPath()
        diamond.moveTo(0.0, -6.2)
        diamond.lineTo(5.8, 0.0)
        diamond.lineTo(0.0, 6.2)
        diamond.lineTo(-5.8, 0.0)
        diamond.closeSubpath()
        painter.drawPath(diamond)
        painter.drawLine(QPointF(-2.5, 0.0), QPointF(2.5, 0.0))

    elif label == "ICO":
        for dx, dy in ((-5.2, -5.2), (1.0, -5.2), (-5.2, 1.0), (1.0, 1.0)):
            painter.drawRoundedRect(QRectF(dx, dy, 4.2, 4.2), 1.0, 1.0)

    elif label == "TIFF":
        painter.drawRoundedRect(QRectF(-5.5, -6.0, 11.0, 8.0), 1.5, 1.5)
        painter.drawLine(QPointF(-4.2, 4.2), QPointF(4.2, 4.2))
        painter.drawLine(QPointF(-2.8, 6.4), QPointF(2.8, 6.4))

    elif label in ("MP3", "MP3 (Audio)"):
        painter.drawEllipse(QPointF(-3.8, 3.6), 2.0, 1.6)
        painter.drawEllipse(QPointF(3.2, 2.4), 2.0, 1.6)
        painter.drawLine(QPointF(-1.8, 3.6), QPointF(-1.8, -4.5))
        painter.drawLine(QPointF(5.2, 2.4), QPointF(5.2, -5.5))
        painter.drawLine(QPointF(-1.8, -4.5), QPointF(5.2, -5.5))

    elif label == "FLAC":
        heights = (2.0, 4.5, 6.4, 4.5, 2.0)
        for idx, h in enumerate(heights):
            x = -5.0 + idx * 2.5
            painter.drawLine(QPointF(x, -h), QPointF(x, h))

    elif label == "OGG":
        painter.drawEllipse(QPointF(-2.0, 0.0), 1.6, 1.6)
        painter.drawArc(QRectF(-5.0, -4.0, 8.0, 8.0), -55 * 16, 110 * 16)
        painter.drawArc(QRectF(-6.0, -6.0, 12.0, 12.0), -55 * 16, 110 * 16)

    elif label == "M4A":
        painter.drawRoundedRect(QRectF(-6.0, -6.0, 12.0, 12.0), 2.5, 2.5)
        painter.drawEllipse(QPointF(-1.0, 2.5), 1.6, 1.3)
        painter.drawLine(QPointF(0.6, 2.5), QPointF(0.6, -3.2))
        painter.drawLine(QPointF(0.6, -3.2), QPointF(3.2, -2.0))

    elif label == "WAV":
        wave = QPainterPath()
        wave.moveTo(-6.5, 0.0)
        wave.lineTo(-4.0, 0.0)
        wave.lineTo(-2.2, -5.5)
        wave.lineTo(0.0, 5.5)
        wave.lineTo(2.2, -3.8)
        wave.lineTo(4.0, 0.0)
        wave.lineTo(6.5, 0.0)
        painter.drawPath(wave)

    elif label == "OPUS":
        painter.drawEllipse(QPointF(0.0, 0.0), 5.8, 5.8)
        painter.drawEllipse(QPointF(0.0, 0.0), 2.4, 2.4)

    elif label == "GIF":
        painter.drawArc(QRectF(-5.5, -5.5, 11.0, 11.0), 30 * 16, 130 * 16)
        painter.drawArc(QRectF(-5.5, -5.5, 11.0, 11.0), 210 * 16, 130 * 16)
        painter.drawLine(QPointF(-5.5, -2.2), QPointF(-5.5, 0.5))
        painter.drawLine(QPointF(-5.5, 0.5), QPointF(-3.0, 0.2))

    elif label == "WEBM":
        painter.drawEllipse(QRectF(-6.0, -6.0, 12.0, 12.0))
        tri = QPainterPath()
        tri.moveTo(-1.8, -2.8)
        tri.lineTo(3.2, 0.0)
        tri.lineTo(-1.8, 2.8)
        tri.closeSubpath()
        painter.drawPath(tri)

    elif label in ("MKV", "MOV"):
        painter.drawRoundedRect(QRectF(-6.5, -5.0, 13.0, 10.0), 1.8, 1.8)
        painter.drawLine(QPointF(-3.5, -5.0), QPointF(-3.5, 5.0))
        painter.drawLine(QPointF(3.5, -5.0), QPointF(3.5, 5.0))

    elif category == MediaCategory.VIDEO or label == "MP4":
        painter.drawRoundedRect(QRectF(-6.5, -5.0, 13.0, 10.0), 2.0, 2.0)
        tri = QPainterPath()
        tri.moveTo(-1.6, -2.6)
        tri.lineTo(2.8, 0.0)
        tri.lineTo(-1.6, 2.6)
        tri.closeSubpath()
        painter.drawPath(tri)

    elif category == MediaCategory.AUDIO:
        heights = (3.0, 6.0, 4.5, 6.0, 3.0)
        for idx, h in enumerate(heights):
            x = -5.0 + idx * 2.5
            painter.drawLine(QPointF(x, -h), QPointF(x, h))

    else:
        painter.drawRoundedRect(QRectF(-6.0, -5.0, 12.0, 10.0), 2.0, 2.0)

    painter.restore()


def paint_tangerine_gem(
    painter: QPainter,
    canvas_size: float,
    scale: float = 1.0,
    opacity: float = 1.0,
    progress: int | None = None,
    with_shadow: bool = True,
) -> None:
    """Paint the 6-segment rounded translucent citrus-gem icon centered on a square canvas."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    center = QPointF(canvas_size / 2.0, canvas_size / 2.0)

    painter.setOpacity(min(1.0, max(0.0, opacity)))
    painter.translate(center)
    painter.scale(scale, scale)

    r_outer = canvas_size * 0.375  # 21px on 56x56 -> 42px gem
    r_seg_in = r_outer * 0.19
    r_seg_out = r_outer * 0.87
    fillet = max(1.2, r_outer * 0.14)

    # 1. Soft ambient radial shadow
    if with_shadow:
        shadow_grad = QRadialGradient(QPointF(0.0, 1.5), canvas_size * 0.48)
        shadow_grad.setColorAt(0.55, QColor(15, 23, 42, 72))
        shadow_grad.setColorAt(0.76, QColor(236, 76, 0, 42))
        shadow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(shadow_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(0.0, 1.5), canvas_size * 0.48, canvas_size * 0.48)

    # 2. Translucent white pith cushion (creates clean 2px pith gaps between carpels)
    outer_rect = QRectF(-r_outer, -r_outer, 2.0 * r_outer, 2.0 * r_outer)
    painter.setBrush(QColor(255, 255, 255, 90))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(outer_rect)

    # 3. 6 Plump Rounded Tangerine Segments (Carpels)
    seg_grad = QLinearGradient(QPointF(-r_seg_out * 0.5, -r_seg_out), QPointF(r_seg_out * 0.5, r_seg_out))
    seg_grad.setColorAt(0.0, QColor(255, 140, 51, 235))  # #FF8C33
    seg_grad.setColorAt(1.0, QColor(236, 76, 0, 235))    # #EC4C00

    for i in range(6):
        mid_deg = 90.0 - i * 60.0
        carpel = _build_rounded_petal_path(
            0.0,
            0.0,
            r_seg_in,
            r_seg_out,
            mid_deg,
            48.5,
            fillet=fillet,
        )
        painter.setBrush(seg_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(carpel)

    # Tiny translucent center pith core
    painter.setBrush(QColor(255, 255, 255, 135))
    painter.drawEllipse(QPointF(0.0, 0.0), r_outer * 0.11, r_outer * 0.11)

    # 4. Curved top-left glass reflection crescent (clipped cleanly inside rim)
    painter.save()
    clip_path = QPainterPath()
    clip_path.addEllipse(outer_rect.adjusted(0.8, 0.8, -0.8, -0.8))
    painter.setClipPath(clip_path)

    crescent_outer = QPainterPath()
    crescent_outer.addEllipse(QRectF(-r_outer * 0.96, -r_outer * 0.96, r_outer * 1.92, r_outer * 1.92))
    crescent_cut = QPainterPath()
    crescent_cut.addEllipse(QRectF(-r_outer * 0.62, -r_outer * 0.56, r_outer * 1.85, r_outer * 1.85))
    crescent = crescent_outer.subtracted(crescent_cut)

    sheen_grad = QLinearGradient(QPointF(-r_outer, -r_outer), QPointF(r_outer * 0.25, r_outer * 0.25))
    sheen_grad.setColorAt(0.0, QColor(255, 255, 255, 110))
    sheen_grad.setColorAt(0.65, QColor(255, 255, 255, 35))
    sheen_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.setBrush(sheen_grad)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPath(crescent)
    painter.restore()

    # 5. Sleek 1.2px translucent white glass rim
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(255, 255, 255, 185), 1.2))
    painter.drawEllipse(outer_rect)

    # 6. Optional active background job progress arc
    if progress is not None:
        pen = QPen(QColor(255, 255, 255, 235), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        span_angle = int(-(progress / 100.0) * 360 * 16)
        painter.drawArc(outer_rect.adjusted(2.2, 2.2, -2.2, -2.2), 90 * 16, span_angle)

    painter.restore()


class _JobRelay(QObject):
    """Main-thread QObject slot relay for QRunnable worker signals."""

    def __init__(self, wheel: RadialWheel, job_id: int) -> None:
        super().__init__(wheel)
        self._wheel = wheel
        self._job_id = job_id

    @pyqtSlot(int)
    def on_progress(self, pct: int) -> None:
        self._wheel._on_job_progress(self._job_id, pct)

    @pyqtSlot(int, int, list)
    def on_finished(self, succeeded: int, failed: int, errors: list) -> None:
        self._wheel._on_job_finished(self._job_id, succeeded, failed)
        self.deleteLater()

    @pyqtSlot(str)
    def on_clipboard_text(self, text: str) -> None:
        self._wheel._copy_to_clipboard(text)


class RadialWheel(QWidget):
    """Fixed 320x320 frosted-acrylic dual-mode Tangerine dial wheel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("TangerineLinux Wheel")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(WHEEL_SIZE, WHEEL_SIZE)

        self._orb: FloatingOrb | None = None
        self._orb_enabled: bool = True
        self._active_route: RouteResult | None = None
        self._mode: WheelMode = WheelMode.CONVERT
        self._slices: tuple[str, ...] = ()
        self._hovered_index: int | None = None
        self._hub_hovered: bool = False
        self._toggle_zone_hovered: bool = False
        self._x11_blur_attempted: bool = False

        self._thread_pool = QThreadPool.globalInstance()
        self._next_job_id: int = 1
        self._job_progress: dict[int, int] = {}
        self._status_text: str = ""

        self._post_conversion_timer = QTimer(self)
        self._post_conversion_timer.setSingleShot(True)
        self._post_conversion_timer.timeout.connect(self._finish_and_return_to_orb)

        # Tactile open/close animation inside paintEvent (no window resize/move)
        self._anim_progress: float = 0.0
        self._is_closing: bool = False
        self._wheel_anim = QVariantAnimation(self)
        self._wheel_anim.valueChanged.connect(self._on_wheel_anim_value)
        self._wheel_anim.finished.connect(self._on_wheel_anim_finished)

        # Pre-rendered static shadow + frosted-glass backdrop pixmap
        self._backdrop_pixmap: QPixmap = self._build_backdrop_pixmap()

    def _build_backdrop_pixmap(self) -> QPixmap:
        """Pre-render outer radial shadow and frosted-acrylic disc once for 60fps painting."""
        dpr = max(1.0, float(self.devicePixelRatioF()))
        pixmap = QPixmap(int(round(WHEEL_SIZE * dpr)), int(round(WHEEL_SIZE * dpr)))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx, cy = self._center()

        shadow_grad = QRadialGradient(QPointF(cx, cy + 2.0), WHEEL_SIZE / 2.0)
        shadow_grad.setColorAt(0.86, QColor(10, 15, 28, 90))
        shadow_grad.setColorAt(0.92, QColor(10, 15, 28, 38))
        shadow_grad.setColorAt(0.97, QColor(10, 15, 28, 12))
        shadow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(shadow_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(0.0, 0.0, WHEEL_SIZE, WHEEL_SIZE))

        chassis_rect = QRectF(cx - CHASSIS_RADIUS, cy - CHASSIS_RADIUS, CHASSIS_DIAMETER, CHASSIS_DIAMETER)
        painter.setBrush(QColor(245, 248, 255, 135))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(chassis_rect)

        sheen_grad = QLinearGradient(chassis_rect.topLeft(), chassis_rect.bottomRight())
        sheen_grad.setColorAt(0.0, QColor(255, 255, 255, 75))
        sheen_grad.setColorAt(1.0, QColor(255, 255, 255, 10))

        rim_grad = QLinearGradient(chassis_rect.topLeft(), chassis_rect.bottomRight())
        rim_grad.setColorAt(0.0, QColor(255, 255, 255, 230))
        rim_grad.setColorAt(1.0, QColor(255, 255, 255, 65))

        painter.setBrush(sheen_grad)
        painter.setPen(QPen(rim_grad, 1.5))
        painter.drawEllipse(chassis_rect)
        painter.end()
        return pixmap

    def hideEvent(self, event: QHideEvent) -> None:
        self._wheel_anim.stop()
        self._post_conversion_timer.stop()
        super().hideEvent(event)

    def set_orb(self, orb: FloatingOrb) -> None:
        """Associate the companion FloatingOrb shown when the wheel closes."""
        self._orb = orb

    def set_orb_enabled(self, enabled: bool) -> None:
        """Control whether FloatingOrb is visible when idle and restored after wheel closes."""
        self._orb_enabled = bool(enabled)
        if self._orb is not None:
            self._orb._enabled = self._orb_enabled
            if self._orb_enabled and not self.isVisible():
                self._orb.pop_in()
            elif not self._orb_enabled:
                self._orb._hover_anim.stop()
                self._orb._pop_anim.stop()
                self._orb.hide()

    def is_orb_enabled(self) -> bool:
        return self._orb_enabled

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._x11_blur_attempted:
            self._x11_blur_attempted = True
            self._request_x11_backdrop_blur()

    def _request_x11_backdrop_blur(self) -> None:
        """Best-effort, non-blocking X11 backdrop blur hint for supporting compositors."""
        try:
            xprop = shutil.which("xprop")
            if not xprop:
                return
            win_id = int(self.winId())
            if win_id <= 0:
                return
            subprocess.Popen(
                [
                    xprop,
                    "-id",
                    str(win_id),
                    "-f",
                    "_KDE_NET_WM_BLUR_BEHIND_REGION",
                    "32c",
                    "-set",
                    "_KDE_NET_WM_BLUR_BEHIND_REGION",
                    "0",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def _position_near_orb(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.move(880, 250)
            return
        avail = screen.availableGeometry()
        if self._orb is not None:
            orb_cy = self._orb.y() + (self._orb.height() // 2)
        else:
            orb_cy = avail.center().y()

        x = max(avail.left() + 8, avail.right() - WHEEL_SIZE - SCREEN_RIGHT_MARGIN)
        y = max(avail.top() + 8, min(orb_cy - (WHEEL_SIZE // 2), avail.bottom() - WHEEL_SIZE - 8))
        self.move(x, y)

    def _position_at_cursor(self, cursor_pos: QPoint) -> None:
        screen = QGuiApplication.screenAt(cursor_pos) or QGuiApplication.primaryScreen()
        x = cursor_pos.x() - (WHEEL_SIZE // 2)
        y = cursor_pos.y() - (WHEEL_SIZE // 2)
        if screen is not None:
            avail = screen.availableGeometry()
            x = max(avail.left() + 8, min(x, avail.right() - WHEEL_SIZE - 8))
            y = max(avail.top() + 8, min(y, avail.bottom() - WHEEL_SIZE - 8))
        self.move(x, y)

    def _on_wheel_anim_value(self, value: object) -> None:
        self._anim_progress = float(value)
        self.update()

    def _on_wheel_anim_finished(self) -> None:
        if self._is_closing:
            self._is_closing = False
            self._slices = ()
            self.hide()
            if self._orb is not None:
                if self._orb_enabled:
                    self._orb.pop_in()
                else:
                    self._orb.hide()

    def open_with_files(
        self,
        file_paths: Sequence[str | Path],
        anchor_pos: QPoint | None = None,
    ) -> bool:
        """Populate slices from file_paths and spring-open the 320x320 glass wheel."""
        route = route_files(file_paths)
        if route is None:
            return False

        self._post_conversion_timer.stop()
        self._wheel_anim.stop()
        self._is_closing = False
        self._active_route = route
        self._mode = route.mode
        self._slices = route.slices
        self._hovered_index = None
        self._hub_hovered = False
        self._toggle_zone_hovered = False
        self._status_text = ""

        if anchor_pos is not None:
            self._position_at_cursor(anchor_pos)
        else:
            self._position_near_orb()

        self._anim_progress = 0.25
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

        self._wheel_anim.setDuration(220)
        self._wheel_anim.setStartValue(0.25)
        self._wheel_anim.setEndValue(1.0)
        self._wheel_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._wheel_anim.start()
        self.update()
        return True

    def open_empty_at_cursor(self, anchor_pos: QPoint) -> None:
        """Spring-open the 320x320 wheel centered at cursor ready for drag-and-drop."""
        self._post_conversion_timer.stop()
        self._wheel_anim.stop()
        self._is_closing = False
        self._active_route = None
        self._mode = WheelMode.CONVERT
        self._slices = ()
        self._hovered_index = None
        self._hub_hovered = False
        self._toggle_zone_hovered = False
        self._status_text = ""

        self._position_at_cursor(anchor_pos)
        self._anim_progress = 0.25
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

        self._wheel_anim.setDuration(220)
        self._wheel_anim.setStartValue(0.25)
        self._wheel_anim.setEndValue(1.0)
        self._wheel_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._wheel_anim.start()
        self.update()

    def toggle_mode(self) -> bool:
        """Toggle between CONVERT and ACTIONS modes when supported by active category."""
        if self._active_route is None:
            return False

        next_mode = (
            WheelMode.ACTIONS if self._mode == WheelMode.CONVERT else WheelMode.CONVERT
        )
        if not supports_mode(self._active_route.category, next_mode):
            return False

        self._mode = next_mode
        new_slices = get_slices(self._active_route.category, next_mode)
        self._active_route = RouteResult(
            category=self._active_route.category,
            mode=next_mode,
            slices=new_slices,
            files=self._active_route.files,
        )
        self._slices = new_slices
        self.update()
        return True

    def cancel_and_close(self) -> None:
        """Collapse-animate the radial wheel and restore the FloatingOrb if enabled."""
        self._post_conversion_timer.stop()
        self._active_route = None
        self._hovered_index = None
        self._hub_hovered = False
        self._toggle_zone_hovered = False

        if not self.isVisible():
            self._is_closing = False
            self._slices = ()
            if self._orb is not None:
                if self._orb_enabled:
                    self._orb.pop_in()
                else:
                    self._orb.hide()
            return

        if self._is_closing:
            return

        self._wheel_anim.stop()
        self._is_closing = True
        start_val = min(1.0, max(0.0, self._anim_progress))
        self._wheel_anim.setDuration(160)
        self._wheel_anim.setStartValue(start_val)
        self._wheel_anim.setEndValue(0.0)
        self._wheel_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._wheel_anim.start()
        self.update()

    def _finish_and_return_to_orb(self) -> None:
        if not self._job_progress and self._active_route is None:
            self._status_text = ""
            self.cancel_and_close()

    def _center(self) -> tuple[float, float]:
        return WHEEL_SIZE / 2.0, WHEEL_SIZE / 2.0

    def _mode_tag_rect(self) -> QRectF:
        cx, cy = self._center()
        return QRectF(cx - 36.0, cy - 39.0, 72.0, 18.0)

    def _mode_dots_rect(self) -> QRectF:
        cx, cy = self._center()
        return QRectF(cx - 22.0, cy + 20.0, 44.0, 18.0)

    def _is_in_toggle_zone(self, pos: QPointF) -> bool:
        if self._active_route is None or not supports_mode(self._active_route.category, WheelMode.CONVERT):
            return False
        return self._mode_tag_rect().contains(pos) or self._mode_dots_rect().contains(pos)

    def _slice_at_pos(self, pos: QPointF) -> int | None:
        if not self._slices:
            return None
        cx, cy = self._center()
        dx = pos.x() - cx
        dy = pos.y() - cy
        dist = math.hypot(dx, dy)

        if dist <= HUB_RADIUS or dist > SLICE_OUTER_RADIUS + HOVER_POP_OUT_PX + 4.0:
            return None

        angle_cw_from_top = math.degrees(math.atan2(dx, -dy)) % 360.0
        idx = int(((angle_cw_from_top + 30.0) % 360.0) // 60.0)
        return idx if 0 <= idx < len(self._slices) else None

    def _is_in_hub(self, pos: QPointF) -> bool:
        cx, cy = self._center()
        return math.hypot(pos.x() - cx, pos.y() - cy) <= HUB_RADIUS

    def _update_hover_state(self, pos: QPointF) -> None:
        new_slice = self._slice_at_pos(pos)
        new_toggle = self._is_in_toggle_zone(pos)
        new_hub = self._is_in_hub(pos) and not new_toggle
        if (
            new_slice != self._hovered_index
            or new_hub != self._hub_hovered
            or new_toggle != self._toggle_zone_hovered
        ):
            self._hovered_index = new_slice
            self._hub_hovered = new_hub
            self._toggle_zone_hovered = new_toggle
            self.update()

    def _trigger_slice(self, target_idx: int) -> None:
        if self._active_route is None or not (0 <= target_idx < len(self._active_route.slices)):
            return
        route = self._active_route
        label = route.slices[target_idx]
        self._hovered_index = None
        self._hub_hovered = False
        self._toggle_zone_hovered = False
        self._active_route = None
        self._start_job(
            route.files,
            route.category,
            route.mode,
            label,
            skipped_count=len(route.skipped_files),
        )

    # --- Drag & Drop Events (Zero-click mode) ---

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._active_route is None:
            paths = _extract_local_paths(event)
            route = route_files(paths)
            if route is None:
                event.ignore()
                return
            self._active_route = route
            self._mode = route.mode
            self._slices = route.slices

        event.acceptProposedAction()
        self._update_hover_state(event.position())

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._active_route is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self._update_hover_state(event.position())

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        # Never auto-close on dragLeaveEvent; wheel stays open until drop, click, or Esc
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        if self._active_route is None:
            route = route_files(_extract_local_paths(event))
            if route is None:
                event.ignore()
                self.cancel_and_close()
                return
            self._active_route = route
            self._mode = route.mode
            self._slices = route.slices

        target_idx = self._slice_at_pos(event.position())
        if target_idx is None:
            target_idx = self._hovered_index

        if target_idx is None:
            event.ignore()
            if not self._job_progress:
                self.cancel_and_close()
            return

        event.acceptProposedAction()
        self._trigger_slice(target_idx)

    # --- Mouse & Keyboard Events (Click-to-Convert Fallback, Mode Toggle & Cancel) ---

    def focusNextPrevChild(self, next: bool) -> bool:
        # Prevent Qt from consuming Tab for focus navigation so Tab toggles wheel mode
        return False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._active_route is not None:
            self._update_hover_state(event.position())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: object) -> None:
        if self._hovered_index is not None or self._hub_hovered or self._toggle_zone_hovered:
            self._hovered_index = None
            self._hub_hovered = False
            self._toggle_zone_hovered = False
            self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton and self._active_route is not None:
            self.toggle_mode()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            if self._is_in_toggle_zone(event.position()):
                self.toggle_mode()
                event.accept()
                return

            if self._is_in_hub(event.position()):
                self.cancel_and_close()
                event.accept()
                return

            if self._active_route is not None:
                target_idx = self._slice_at_pos(event.position())
                if target_idx is not None:
                    self._trigger_slice(target_idx)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_and_close()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Tab and self._active_route is not None:
            self.toggle_mode()
            event.accept()
            return
        super().keyPressEvent(event)

    # --- Asynchronous Execution ---

    def _start_job(
        self,
        files: tuple[Path, ...],
        category: MediaCategory,
        mode: WheelMode,
        slice_label: str,
        skipped_count: int = 0,
    ) -> None:
        job_id = self._next_job_id
        self._next_job_id += 1
        self._job_progress[job_id] = 0
        self._status_text = "0%"
        self._post_conversion_timer.stop()

        relay = _JobRelay(self, job_id)
        if mode == WheelMode.ACTIONS:
            worker = ActionWorker(files, category, slice_label, skipped_count=skipped_count)
            worker.signals.clipboard_text.connect(
                relay.on_clipboard_text,
                Qt.ConnectionType.QueuedConnection,
            )
        else:
            worker = ConversionWorker(files, category, slice_label, skipped_count=skipped_count)

        worker.signals.progress.connect(
            relay.on_progress,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.signals.finished.connect(
            relay.on_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._thread_pool.start(worker)
        self.update()

    def _copy_to_clipboard(self, text: str) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None and text:
            clipboard.setText(text)

    def _overall_progress(self) -> int:
        if not self._job_progress:
            return 0
        return int(sum(self._job_progress.values()) / len(self._job_progress))

    def _on_job_progress(self, job_id: int, pct: int) -> None:
        if job_id in self._job_progress:
            self._job_progress[job_id] = pct
            self._status_text = f"{self._overall_progress()}%"
            self.update()
            if self._orb is not None and self._orb.isVisible():
                self._orb.update()

    def _on_job_finished(self, job_id: int, succeeded: int, failed: int) -> None:
        self._job_progress.pop(job_id, None)
        if not self._job_progress:
            self._status_text = "Done" if failed == 0 else "Error"
            self.update()
            if self._orb is not None and self._orb.isVisible():
                self._orb.update()
            if self.isVisible() and self._active_route is None:
                self._post_conversion_timer.start(600)
        else:
            self._status_text = f"{self._overall_progress()}%"
            self.update()

    # --- Painting ---

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        cx, cy = self._center()
        p = self._anim_progress
        opacity = min(1.0, max(0.0, p))
        if self._is_closing:
            scale = 0.70 + 0.30 * p
        else:
            scale = 0.72 + 0.28 * p
        rot_deg = -10.0 * (1.0 - p)

        painter.setOpacity(opacity)
        painter.translate(cx, cy)
        painter.rotate(rot_deg)
        painter.scale(scale, scale)
        painter.translate(-cx, -cy)

        # 1 & 2. Cached multi-stop radial ambient shadow + frosted acrylic glass chassis
        painter.drawPixmap(0, 0, self._backdrop_pixmap)

        slices = self._slices if self._slices else ("", "", "", "", "", "")
        category = self._active_route.category if self._active_route is not None else None

        # 3. 6 Rounded Pillowy Petals (6px corner fillets, 4° gap, signature Tangerine hover)
        for i in range(SLICE_COUNT):
            mid_deg = 90.0 - i * 60.0
            mid_rad = math.radians(mid_deg)

            is_hovered = self._hovered_index == i and self._active_route is not None
            offset = HOVER_POP_OUT_PX if is_hovered else 0.0
            scx = cx + offset * math.cos(mid_rad)
            scy = cy - offset * math.sin(mid_rad)

            petal_path = _build_rounded_petal_path(
                scx,
                scy,
                SLICE_INNER_RADIUS,
                SLICE_OUTER_RADIUS,
                mid_deg,
                SLICE_SWEEP_DEG,
                PETAL_CORNER_RADIUS,
            )

            if is_hovered:
                # Warm tangerine outer glow ring behind popped-out petal
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(255, 115, 20, 95), 4.0))
                painter.drawPath(petal_path)

                tangerine_grad = QLinearGradient(
                    QPointF(scx, scy - SLICE_OUTER_RADIUS),
                    QPointF(scx, scy + SLICE_OUTER_RADIUS),
                )
                tangerine_grad.setColorAt(0.0, TANGERINE_TOP)
                tangerine_grad.setColorAt(1.0, TANGERINE_BOTTOM)
                painter.setBrush(tangerine_grad)
                painter.setPen(QPen(QColor(255, 255, 255, 215), 1.2))
                painter.drawPath(petal_path)
            else:
                # Subtle translucent drop shadow under idle glass petal
                shadow_path = QPainterPath(petal_path)
                shadow_path.translate(0.0, 1.2)
                painter.setBrush(QColor(15, 23, 42, 16))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawPath(shadow_path)

                milk_grad = QLinearGradient(
                    QPointF(scx, scy - SLICE_OUTER_RADIUS),
                    QPointF(scx, scy + SLICE_OUTER_RADIUS),
                )
                milk_grad.setColorAt(0.0, QColor(255, 255, 255, 175))
                milk_grad.setColorAt(1.0, QColor(235, 240, 248, 145))
                painter.setBrush(milk_grad)

                spec_pen_grad = QLinearGradient(
                    QPointF(scx - SLICE_OUTER_RADIUS, scy - SLICE_OUTER_RADIUS),
                    QPointF(scx + SLICE_OUTER_RADIUS, scy + SLICE_OUTER_RADIUS),
                )
                spec_pen_grad.setColorAt(0.0, QColor(255, 255, 255, 210))
                spec_pen_grad.setColorAt(1.0, QColor(255, 255, 255, 95))
                painter.setPen(QPen(spec_pen_grad, 1.0))
                painter.drawPath(petal_path)

            label = slices[i] if i < len(slices) else ""
            if label:
                content_r = (SLICE_OUTER_RADIUS + SLICE_INNER_RADIUS) / 2.0
                lx = scx + content_r * math.cos(mid_rad)
                ly = scy - content_r * math.sin(mid_rad)

                text_color = QColor("#FFFFFF") if is_hovered else QColor("#111827")
                icon_color = QColor("#FFFFFF") if is_hovered else QColor("#111827")
                _draw_slice_icon(painter, label, category, QPointF(lx, ly - 11.0), icon_color)

                font_size = 10 if len(label) <= 10 else 9
                font = QFont("Sans Serif", font_size, QFont.Weight.Bold if is_hovered else QFont.Weight.DemiBold)
                painter.setFont(font)
                painter.setPen(text_color)
                painter.drawText(
                    QRectF(lx - 40.0, ly - 1.0, 80.0, 26.0),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                    label,
                )

        # 4. Recessed Translucent Center Well (radius 54px)
        hub_rect = QRectF(cx - HUB_RADIUS, cy - HUB_RADIUS, 2.0 * HUB_RADIUS, 2.0 * HUB_RADIUS)
        tangerine_ring = QColor(TANGERINE_RING_HEX)

        if self._hub_hovered and not self._job_progress and not self._status_text:
            hub_fill = QColor(254, 226, 226, 220)
            hub_border = QColor("#EF4444")
        else:
            hub_fill = QColor(255, 255, 255, 195)
            hub_border = tangerine_ring

        painter.setBrush(hub_fill)
        painter.setPen(QPen(hub_border, 1.5))
        painter.drawEllipse(hub_rect)

        # Subtle inner bevel shadow inside center well
        bevel_grad = QRadialGradient(QPointF(cx, cy + 2.0), HUB_RADIUS)
        bevel_grad.setColorAt(0.75, QColor(15, 23, 42, 0))
        bevel_grad.setColorAt(0.95, QColor(15, 23, 42, 18))
        bevel_grad.setColorAt(1.0, QColor(15, 23, 42, 28))
        painter.setBrush(bevel_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(hub_rect.adjusted(1.5, 1.5, -1.5, -1.5))

        if self._job_progress or self._status_text:
            self._paint_hub_progress(painter, hub_rect, tangerine_ring)
        elif self._hub_hovered:
            painter.setFont(QFont("Sans Serif", 11, QFont.Weight.Bold))
            painter.setPen(QColor("#DC2626"))
            painter.drawText(hub_rect, Qt.AlignmentFlag.AlignCenter, "✕ CANCEL")
        else:
            self._paint_hub_idle(painter, cx, cy, tangerine_ring)

    def _paint_hub_progress(self, painter: QPainter, hub_rect: QRectF, ring_color: QColor) -> None:
        pct = 100 if not self._job_progress and self._status_text == "Done" else self._overall_progress()
        ring_rect = hub_rect.adjusted(5.0, 5.0, -5.0, -5.0)

        painter.setPen(QPen(QColor(15, 23, 42, 24), 4.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(ring_rect)

        if self._status_text == "Done":
            arc_color = QColor("#16A34A")
        elif self._status_text == "Error":
            arc_color = QColor("#DC2626")
        else:
            arc_color = ring_color

        painter.setPen(QPen(arc_color, 4.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        span_angle = int(-(pct / 100.0) * 360 * 16)
        painter.drawArc(ring_rect, 90 * 16, span_angle)

        if self._status_text == "Done":
            cx, cy = hub_rect.center().x(), hub_rect.center().y()
            check_pen = QPen(QColor("#16A34A"), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(check_pen)
            check = QPainterPath()
            check.moveTo(cx - 12.0, cy + 1.0)
            check.lineTo(cx - 3.0, cy + 10.0)
            check.lineTo(cx + 13.0, cy - 9.0)
            painter.drawPath(check)
        else:
            painter.setFont(QFont("Sans Serif", 12, QFont.Weight.Bold))
            painter.setPen(QColor("#0F172A"))
            painter.drawText(hub_rect, Qt.AlignmentFlag.AlignCenter, self._status_text)

    def _paint_hub_idle(self, painter: QPainter, cx: float, cy: float, tangerine_ring: QColor) -> None:
        # 1. Top tiny mode badge (CONVERT in #FF5E00 or ACTIONS in #7C3AED, 8pt bold)
        tag_rect = self._mode_tag_rect()
        badge_color = (
            QColor(ACTIONS_BADGE_HEX)
            if self._mode == WheelMode.ACTIONS
            else QColor(TANGERINE_RING_HEX)
        )
        if self._toggle_zone_hovered:
            painter.setBrush(QColor(badge_color.red(), badge_color.green(), badge_color.blue(), 28))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(tag_rect, 9.0, 9.0)

        painter.setFont(QFont("Sans Serif", 8, QFont.Weight.Bold))
        painter.setPen(badge_color)
        painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, self._mode.value)

        # 2. Center bold live target (14pt bold #0F172A, auto-scaled cleanly for long action names)
        preview_text = ""
        if self._hovered_index is not None and self._hovered_index < len(self._slices):
            preview_text = f"→ {self._slices[self._hovered_index]}"
        elif self._active_route is not None:
            preview_text = _format_file_badge(self._active_route)
        else:
            preview_text = "Drop File"

        if preview_text:
            preview_rect = QRectF(cx - 47.0, cy - 15.0, 94.0, 30.0)
            font_pt = 14
            font = QFont("Sans Serif", font_pt, QFont.Weight.Bold)
            while font_pt > 9 and QFontMetrics(font).horizontalAdvance(preview_text) > 90:
                font_pt -= 1
                font.setPointSize(font_pt)
            painter.setFont(font)
            painter.setPen(QColor("#0F172A"))
            painter.drawText(preview_rect, Qt.AlignmentFlag.AlignCenter, preview_text)

        # 3. Bottom two tiny mode dots (● ○ vs ○ ●)
        can_toggle = (
            self._active_route is not None
            and supports_mode(self._active_route.category, WheelMode.CONVERT)
        )
        dot_y = cy + 29.0
        left_dot = QPointF(cx - 6.5, dot_y)
        right_dot = QPointF(cx + 6.5, dot_y)
        dot_r = 3.2

        if can_toggle:
            convert_active = self._mode == WheelMode.CONVERT
            for pt, active in ((left_dot, convert_active), (right_dot, not convert_active)):
                if active:
                    painter.setBrush(badge_color)
                    painter.setPen(Qt.PenStyle.NoPen)
                else:
                    painter.setBrush(QColor(255, 255, 255, 180))
                    painter.setPen(QPen(QColor(148, 163, 184, 185), 1.2))
                painter.drawEllipse(pt, dot_r, dot_r)
        else:
            painter.setBrush(badge_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(cx, dot_y), dot_r, dot_r)


class FloatingOrb(QWidget):
    """Fixed 56x56 frameless, always-on-top, translucent citrus-gem orb."""

    def __init__(
        self,
        radial_wheel: RadialWheel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._radial_wheel = radial_wheel
        self._radial_wheel.set_orb(self)
        self._is_hovered: bool = False
        self._enabled: bool = True

        self._hover_scale: float = 1.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(130)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_scale)

        self._pop_progress: float = 1.0
        self._pop_anim = QVariantAnimation(self)
        self._pop_anim.setDuration(150)
        self._pop_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._pop_anim.valueChanged.connect(self._on_pop_progress)

        self.setWindowTitle("TangerineLinux")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setFixedSize(ORB_WINDOW_SIZE, ORB_WINDOW_SIZE)

        self._dock_right_edge()

    def _on_hover_scale(self, value: object) -> None:
        self._hover_scale = float(value)
        self.update()

    def _on_pop_progress(self, value: object) -> None:
        self._pop_progress = float(value)
        self.update()

    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._radial_wheel._orb_enabled = self._enabled
        if self._enabled:
            if not self._radial_wheel.isVisible():
                self.pop_in()
        else:
            self._hover_anim.stop()
            self._pop_anim.stop()
            self.hide()

    def pop_in(self) -> None:
        """Smoothly fade and pop the FloatingOrb back into view."""
        self.reset_hover()
        self._pop_anim.stop()
        self._pop_progress = 0.0
        self.show()
        self.raise_()
        self._pop_anim.setStartValue(0.0)
        self._pop_anim.setEndValue(1.0)
        self._pop_anim.start()
        self.update()

    def reset_hover(self) -> None:
        self._is_hovered = False
        self._hover_anim.stop()
        self._hover_scale = 1.0

    def _dock_right_edge(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.move(1150, 380)
            return
        avail = screen.availableGeometry()
        x = max(avail.left() + 8, avail.right() - ORB_WINDOW_SIZE - SCREEN_RIGHT_MARGIN)
        y = max(avail.top() + 8, min(avail.center().y() - (ORB_WINDOW_SIZE // 2), avail.bottom() - ORB_WINDOW_SIZE - 8))
        self.move(x, y)

    def enterEvent(self, event: QEnterEvent) -> None:
        self._is_hovered = True
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_scale)
        self._hover_anim.setEndValue(1.08)
        self._hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event: object) -> None:
        self._is_hovered = False
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_scale)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        paths = _extract_local_paths(event)
        if not paths or route_files(paths) is None:
            event.ignore()
            return

        event.acceptProposedAction()
        self._is_hovered = True
        self._hover_scale = 1.08
        if self._radial_wheel.open_with_files(paths):
            self.reset_hover()
            self.hide()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        pop_scale = 0.80 + 0.20 * self._pop_progress
        scale = self._hover_scale * pop_scale
        opacity = min(1.0, max(0.0, self._pop_progress))
        progress = self._radial_wheel._overall_progress() if self._radial_wheel._job_progress else None
        paint_tangerine_gem(
            painter,
            float(ORB_WINDOW_SIZE),
            scale=scale,
            opacity=opacity,
            progress=progress,
            with_shadow=True,
        )
