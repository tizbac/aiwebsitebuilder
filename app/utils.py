"""Shared helpers: image encoding, formatting."""

from __future__ import annotations

import base64
import mimetypes
import os
import uuid
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt6.QtGui import QImage, QPainter


_IMAGE_CACHE: dict[tuple[str, float, int], str] = {}


def human_tokens(value: int | float | None) -> str:
    n = float(value or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1_000:
        return f"{n / 1000:.1f}k"
    return str(int(n))


def human_bytes(value: int) -> str:
    n = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def mime_for_image(path: str | Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "image/png"


def image_dimensions(path: str | Path) -> tuple[int, int]:
    img = QImage(str(path))
    if img.isNull():
        return (0, 0)
    return (img.width(), img.height())


def _encode_image(img: QImage, quality: int = 86) -> tuple[bytes, str]:
    """Encode a QImage as JPEG (white background) and return bytes + mime."""
    canvas = QImage(img.size(), QImage.Format.Format_RGB32)
    canvas.fill(Qt.GlobalColor.white)
    painter = QPainter(canvas)
    painter.drawImage(0, 0, img)
    painter.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    canvas.save(buf, "JPEG", quality)
    buf.close()
    return bytes(data), "image/jpeg"


def scaled_image(path: str | Path, max_px: int) -> QImage:
    img = QImage(str(path))
    if img.isNull():
        raise ValueError(f"Could not read image: {path}")
    if max_px > 0 and max(img.width(), img.height()) > max_px:
        img = img.scaled(
            max_px,
            max_px,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return img


def image_data_uri(path: str | Path, max_px: int = 1568) -> str:
    path = Path(path)
    try:
        stamp = path.stat().st_mtime
    except OSError:
        stamp = 0.0
    key = (str(path), stamp, int(max_px))
    cached = _IMAGE_CACHE.get(key)
    if cached:
        return cached
    try:
        img = scaled_image(path, max_px)
        raw, mime = _encode_image(img)
    except (ValueError, OSError):
        raw = path.read_bytes()
        mime = mime_for_image(path)
    uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    if len(_IMAGE_CACHE) > 40:
        _IMAGE_CACHE.clear()
    _IMAGE_CACHE[key] = uri
    return uri


def import_attachment(source: str | Path, dest_dir: str | Path, max_px: int = 2048) -> str:
    """Copy/normalise a user image into the project and return its path."""
    source = Path(source)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        suffix = ".png"
    name = f"ref_{uuid.uuid4().hex[:10]}{suffix}"
    dest = dest_dir / name
    try:
        img = scaled_image(source, max_px)
        if suffix in (".jpg", ".jpeg"):
            img.save(str(dest), "JPEG", 90)
        elif suffix == ".webp":
            img.save(str(dest), "WEBP", 90)
        else:
            img.save(str(dest), "PNG")
    except (ValueError, OSError) as exc:
        raise ValueError(f"Unsupported or unreadable image '{source.name}': {exc}") from exc
    return str(dest)


def save_clipboard_image(image: QImage, dest_dir: str | Path) -> str:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"ref_{uuid.uuid4().hex[:10]}.png"
    if max(image.width(), image.height()) > 2048:
        image = image.scaled(
            2048,
            2048,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    image.save(str(dest), "PNG")
    return str(dest)


def format_seconds(ts: float | None) -> str:
    import datetime

    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M")


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
