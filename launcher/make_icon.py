"""Build launcher/APRSBox.ico from app/static/media/aprs-box-icon.png (the box of the logo).

    .venv\\Scripts\\python launcher\\make_icon.py

Small sizes are 32-bit DIBs (classic icon entries), 128 and 256 px are PNG entries.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "app" / "static" / "media" / "aprs-box-icon.png"
TARGET = Path(__file__).resolve().parent / "APRSBox.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
PNG_FROM = 128


def square(image: QImage) -> QImage:
    side = max(image.width(), image.height())
    canvas = QImage(side, side, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawImage((side - image.width()) // 2, (side - image.height()) // 2, image)
    painter.end()
    return canvas


def dib_entry(image: QImage) -> bytes:
    size = image.width()
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = bytearray()
    for y in range(size - 1, -1, -1):  # bottom-up BGRA
        for x in range(size):
            color = image.pixelColor(x, y)
            pixels += bytes((color.blue(), color.green(), color.red(), color.alpha()))
    mask_row = ((size + 31) // 32) * 4  # the AND mask: all zero, alpha does the job
    return header + bytes(pixels) + bytes(mask_row * size)


def png_entry(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data.data())


def main() -> int:
    QGuiApplication(sys.argv[:1] + ["-platform", "offscreen"])
    source = square(QImage(str(SOURCE)).convertToFormat(QImage.Format.Format_ARGB32))
    entries = []
    for size in SIZES:
        image = source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        image = image.convertToFormat(QImage.Format.Format_ARGB32)
        entries.append((size, png_entry(image) if size >= PNG_FROM else dib_entry(image)))
    offset = 6 + 16 * len(entries)
    directory = b""
    for size, data in entries:
        directory += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    TARGET.write_bytes(struct.pack("<HHH", 0, 1, len(entries)) + directory + b"".join(data for _size, data in entries))
    print(f"{TARGET} ({len(entries)} sizes, {TARGET.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
