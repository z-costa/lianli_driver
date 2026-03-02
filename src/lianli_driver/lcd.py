from __future__ import annotations

import io
from datetime import datetime
from typing import Iterable

try:
    from PIL import Image, ImageDraw, ImageFont

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def rgb888_to_rgb565(r: int, g: int, b: int) -> int:
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def rgb_image_to_rgb565_bytes(rgb_bytes: bytes) -> bytes:
    if len(rgb_bytes) % 3 != 0:
        raise ValueError("RGB byte stream length must be divisible by 3.")
    out = bytearray((len(rgb_bytes) // 3) * 2)
    out_idx = 0
    for i in range(0, len(rgb_bytes), 3):
        value = rgb888_to_rgb565(rgb_bytes[i], rgb_bytes[i + 1], rgb_bytes[i + 2])
        out[out_idx] = value & 0xFF
        out[out_idx + 1] = (value >> 8) & 0xFF
        out_idx += 2
    return bytes(out)


def load_image_as_rgb565(path: str, width: int, height: int) -> bytes:
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is required for image loading. Install with: pip install .[lcd]")
    image = Image.open(path).convert("RGB")
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    return rgb_image_to_rgb565_bytes(image.tobytes())


def load_image_as_jpeg(path: str, width: int, height: int, quality: int = 90) -> bytes:
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is required for image loading. Install with: pip install .[lcd]")
    image = Image.open(path).convert("RGB")
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    # Use a conservative baseline JPEG profile for broader hardware decoder compatibility.
    image.save(
        out,
        format="JPEG",
        quality=max(1, min(100, int(quality))),
        optimize=False,
        progressive=False,
        subsampling=0,
    )
    return out.getvalue()


def generate_clock_frame_rgb565(width: int, height: int, timestamp: datetime | None = None) -> bytes:
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is required for synthetic clock frames.")
    now = timestamp or datetime.now()
    image = Image.new("RGB", (width, height), color=(10, 18, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width - 1, height - 1), outline=(80, 140, 200), width=2)
    draw.text((10, 10), now.strftime("%H:%M:%S"), fill=(220, 235, 255), font=ImageFont.load_default())
    draw.text((10, 25), now.strftime("%Y-%m-%d"), fill=(180, 210, 245), font=ImageFont.load_default())
    return rgb_image_to_rgb565_bytes(image.tobytes())


def chunk_bytes(payload: bytes, chunk_size: int) -> Iterable[bytes]:
    for i in range(0, len(payload), chunk_size):
        yield payload[i : i + chunk_size]


def build_report_packets(payload: bytes, report_size: int = 64, report_id: int = 0) -> list[bytes]:
    if report_size < 2:
        raise ValueError("report_size must be at least 2 bytes.")
    chunk_size = report_size - 1
    packets: list[bytes] = []
    for chunk in chunk_bytes(payload, chunk_size):
        packet = bytes([report_id]) + chunk
        if len(packet) < report_size:
            packet += bytes(report_size - len(packet))
        packets.append(packet)
    return packets
