from __future__ import annotations

from dataclasses import dataclass, field

from ..hidraw import HidRawDevice, write_hid_report
from ..lcd import chunk_bytes
from ..protocol import HidProtocolProfile
from ..util import ActionResult


@dataclass(slots=True)
class LianLiUsbDevice:
    hid: HidRawDevice
    model: str
    capabilities: set[str] = field(default_factory=set)
    protocol: HidProtocolProfile | None = None

    @property
    def key(self) -> str:
        return f"{self.hid.vendor_id:04x}:{self.hid.product_id:04x}"

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.hid.path,
            "model": self.model,
            "vendor_id": f"0x{self.hid.vendor_id:04x}",
            "product_id": f"0x{self.hid.product_id:04x}",
            "name": self.hid.name,
            "capabilities": sorted(self.capabilities),
            "accessible": self.hid.accessible,
            "protocol_loaded": self.protocol is not None,
        }

    def upload_lcd_rgb565(self, frame: bytes, unsafe_hid_writes: bool = False) -> ActionResult:
        if self.protocol is None or self.protocol.lcd is None:
            notes = f" {self.protocol.notes}" if self.protocol and self.protocol.notes else ""
            return ActionResult(
                False,
                "No LCD protocol profile loaded for this VID:PID. "
                "Create ~/.config/lianli-driver/protocols.json."
                f"{notes}",
            )
        if self.protocol.transport != "hid":
            return ActionResult(
                False,
                f"Protocol transport mismatch for HID path: {self.protocol.transport}",
            )
        if not unsafe_hid_writes:
            return ActionResult(
                False,
                "Refusing to write unverified HID protocol. Re-run with --unsafe-hid-writes.",
            )
        if not self.hid.accessible:
            return ActionResult(False, f"Device is not accessible: {self.hid.error or 'permission denied'}")

        lcd = self.protocol.lcd
        assert lcd is not None

        packets = 0
        try:
            if lcd.begin:
                packet = self._pack_report(lcd.begin)
                write_hid_report(self.hid.path, packet)
                packets += 1

            seq = 0
            for chunk in chunk_bytes(frame, lcd.chunk_data_size):
                payload = bytearray()
                payload.extend(lcd.chunk_prefix)
                if lcd.include_sequence_le16:
                    payload.extend(seq.to_bytes(2, byteorder="little", signed=False))
                payload.extend(chunk)
                packet = self._pack_report(bytes(payload))
                write_hid_report(self.hid.path, packet)
                packets += 1
                seq += 1

            if lcd.end:
                packet = self._pack_report(lcd.end)
                write_hid_report(self.hid.path, packet)
                packets += 1
        except OSError as exc:
            return ActionResult(False, f"HID write failed on {self.hid.path}: {exc}", {"packets": packets})

        return ActionResult(True, f"Sent LCD frame to {self.hid.path}.", {"packets": packets})

    def _pack_report(self, payload: bytes) -> bytes:
        if self.protocol is None:
            raise RuntimeError("No protocol profile set.")
        report_size = self.protocol.report_size
        report_id = self.protocol.report_id
        packet = bytes([report_id]) + payload
        if len(packet) < report_size:
            packet += bytes(report_size - len(packet))
        return packet[:report_size]
