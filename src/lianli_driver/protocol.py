from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .constants import DEFAULT_PROTOCOL_FILE


def _hex_to_bytes(value: str) -> bytes:
    compact = value.replace(" ", "").replace(":", "")
    if len(compact) % 2:
        raise ValueError("Hex string must have an even number of characters.")
    return bytes.fromhex(compact)


@dataclass(slots=True)
class LcdProtocol:
    begin: bytes
    chunk_prefix: bytes
    end: bytes
    chunk_data_size: int = 56
    include_sequence_le16: bool = True
    mode: str = "framed"


@dataclass(slots=True)
class HidProtocolProfile:
    name: str
    transport: str = "hid"
    report_size: int = 64
    report_id: int = 0
    out_endpoint: int = 0x01
    in_endpoint: int = 0x81
    packet_size: int = 64
    notes: str = ""
    lcd: LcdProtocol | None = None


def _builtin_profiles() -> dict[str, HidProtocolProfile]:
    return {
        # Legacy/UNI hub style HID transport.
        "0cf2:a102": HidProtocolProfile(
            name="UNI FAN TL Controller",
            transport="hid",
            report_size=64,
            report_id=0,
            packet_size=64,
            notes="Fan/ARGB control confirmed in uni-sync/liquidctl family.",
        ),
        "1a86:2107": HidProtocolProfile(
            name="SLV3H HID Bridge",
            transport="hid",
            report_size=64,
            report_id=0,
            packet_size=64,
            notes=(
                "Bridge/controller detected. LCD image upload protocol is not yet confirmed "
                "for this VID:PID."
            ),
        ),
        # HydroShift II / SL-LCD wireless paths are bulk USB interfaces.
        "1cbe:a021": HidProtocolProfile(
            name="HydroShift II H2 USB",
            transport="usb_bulk",
            out_endpoint=0x01,
            in_endpoint=0x81,
            packet_size=512,
            notes=(
                "Bulk USB transport detected. Static image protocol is not public; "
                "GA II family appears to use host-streamed video."
            ),
        ),
        "1cbe:0005": HidProtocolProfile(
            name="SL-LCD Wireless",
            transport="usb_bulk",
            out_endpoint=0x01,
            in_endpoint=0x81,
            packet_size=512,
            notes="Bulk USB transport detected for SL-LCD wireless receiver.",
        ),
        "0416:8040": HidProtocolProfile(
            name="SLV3TX Wireless TX",
            transport="usb_bulk",
            out_endpoint=0x01,
            in_endpoint=0x81,
            packet_size=64,
            notes="Bulk USB wireless transmitter path.",
        ),
        "0416:8041": HidProtocolProfile(
            name="SLV3RX Wireless RX",
            transport="usb_bulk",
            out_endpoint=0x01,
            in_endpoint=0x81,
            packet_size=64,
            notes="Bulk USB wireless receiver path.",
        ),
        # liquidctl GA II LCD (for reference devices that expose this VID:PID).
        "0416:7395": HidProtocolProfile(
            name="Lian Li GA II LCD",
            transport="hid",
            report_size=64,
            report_id=0,
            packet_size=64,
            notes=(
                "Liquidctl supports monitoring/lighting; screen mode appears to require "
                "continuous host stream."
            ),
        ),
    }


def _profile_from_payload(key: str, payload: dict[str, object]) -> HidProtocolProfile:
    name = str(payload.get("name", key))
    transport = str(payload.get("transport", "hid")).lower()
    report_size = int(payload.get("report_size", 64))
    report_id = int(payload.get("report_id", 0))
    out_endpoint = int(payload.get("out_endpoint", 0x01))
    in_endpoint = int(payload.get("in_endpoint", 0x81))
    packet_size = int(payload.get("packet_size", 64 if transport == "hid" else 512))
    notes = str(payload.get("notes", ""))
    lcd_payload = payload.get("lcd")
    lcd = None
    if isinstance(lcd_payload, dict):
        lcd = LcdProtocol(
            begin=_hex_to_bytes(str(lcd_payload.get("begin", ""))),
            chunk_prefix=_hex_to_bytes(str(lcd_payload.get("chunk_prefix", ""))),
            end=_hex_to_bytes(str(lcd_payload.get("end", ""))),
            chunk_data_size=int(lcd_payload.get("chunk_data_size", 56)),
            include_sequence_le16=bool(lcd_payload.get("include_sequence_le16", True)),
            mode=str(lcd_payload.get("mode", "framed")),
        )
    return HidProtocolProfile(
        name=name,
        transport=transport,
        report_size=report_size,
        report_id=report_id,
        out_endpoint=out_endpoint,
        in_endpoint=in_endpoint,
        packet_size=packet_size,
        notes=notes,
        lcd=lcd,
    )


class ProtocolRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_PROTOCOL_FILE
        self._profiles: dict[str, HidProtocolProfile] = {}
        self.reload()

    def reload(self) -> None:
        self._profiles = _builtin_profiles()
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for key, payload in raw.items():
                if not isinstance(payload, dict):
                    continue
                self._profiles[key.lower()] = _profile_from_payload(key, payload)

    def get(self, vendor_id: int, product_id: int) -> HidProtocolProfile | None:
        return self._profiles.get(f"{vendor_id:04x}:{product_id:04x}")
