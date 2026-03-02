from __future__ import annotations

from dataclasses import dataclass, field

from ..lcd import chunk_bytes
from ..protocol import HidProtocolProfile
from ..usb_bulk import UsbBulkDevice, read_usb_bulk_packet, write_usb_bulk_packet
from ..util import ActionResult


@dataclass(slots=True)
class LianLiUsbBulkDevice:
    usb: UsbBulkDevice
    model: str
    capabilities: set[str] = field(default_factory=set)
    protocol: HidProtocolProfile | None = None

    @property
    def key(self) -> str:
        return f"{self.usb.vendor_id:04x}:{self.usb.product_id:04x}"

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.usb.id,
            "dev_node": self.usb.dev_node,
            "model": self.model,
            "vendor_id": f"0x{self.usb.vendor_id:04x}",
            "product_id": f"0x{self.usb.product_id:04x}",
            "manufacturer": self.usb.manufacturer,
            "product": self.usb.product,
            "serial": self.usb.serial,
            "capabilities": sorted(self.capabilities),
            "accessible": self.usb.accessible,
            "protocol_loaded": self.protocol is not None,
            "transport": "usb_bulk",
            "notes": self.protocol.notes if self.protocol else "",
            "endpoints": [ep.as_dict() for ep in self.usb.endpoints],
        }

    def upload_lcd_rgb565(self, frame: bytes, unsafe_hid_writes: bool = False) -> ActionResult:
        if self.protocol is None:
            return ActionResult(
                False,
                "No protocol profile loaded for this USB device. "
                "Create ~/.config/lianli-driver/protocols.json.",
            )
        if self.protocol.transport != "usb_bulk":
            return ActionResult(False, f"Protocol transport mismatch: {self.protocol.transport}")
        if self.protocol.lcd is None:
            notes = f" {self.protocol.notes}" if self.protocol.notes else ""
            return ActionResult(
                False,
                "LCD image upload protocol is not defined for this device."
                f"{notes}",
            )
        if not unsafe_hid_writes:
            return ActionResult(
                False,
                "Refusing to write unverified device protocol. Re-run with --unsafe-hid-writes.",
            )
        if not self.usb.accessible:
            return ActionResult(
                False,
                f"USB device is not accessible ({self.usb.dev_node}): {self.usb.error or 'permission denied'}",
            )

        lcd = self.protocol.lcd
        packet_size = max(8, int(self.protocol.packet_size))
        endpoint = int(self.protocol.out_endpoint)
        packets = 0

        def _bulk_send(payload: bytes) -> None:
            nonlocal packets
            for chunk in chunk_bytes(payload, packet_size):
                out = bytes(chunk)
                if len(out) < packet_size:
                    out = out + bytes(packet_size - len(out))
                write_usb_bulk_packet(self.usb, endpoint=endpoint, payload=out)
                packets += 1

        try:
            if lcd.mode == "raw":
                _bulk_send(frame)
            else:
                if lcd.begin:
                    _bulk_send(lcd.begin)
                seq = 0
                for chunk in chunk_bytes(frame, lcd.chunk_data_size):
                    payload = bytearray()
                    payload.extend(lcd.chunk_prefix)
                    if lcd.include_sequence_le16:
                        payload.extend(seq.to_bytes(2, byteorder="little", signed=False))
                    payload.extend(chunk)
                    _bulk_send(bytes(payload))
                    seq += 1
                if lcd.end:
                    _bulk_send(lcd.end)
        except Exception as exc:  # noqa: BLE001
            return ActionResult(
                False,
                f"USB bulk write failed on {self.usb.id}: {exc}",
                {"packets": packets, "endpoint": f"0x{endpoint:02x}"},
            )

        return ActionResult(
            True,
            f"Sent LCD frame to {self.usb.id}.",
            {"packets": packets, "endpoint": f"0x{endpoint:02x}"},
        )

    def probe_ga2_style_channel(self) -> ActionResult:
        if not self.usb.accessible:
            return ActionResult(
                False,
                f"USB device is not accessible ({self.usb.dev_node}): {self.usb.error or 'permission denied'}",
            )
        if self.protocol is None or self.protocol.transport != "usb_bulk":
            return ActionResult(False, "No bulk protocol available for probe.")

        packet_size = max(64, int(self.protocol.packet_size))
        out_ep = int(self.protocol.out_endpoint)
        in_ep = int(self.protocol.in_endpoint)
        # liquidctl GA2-style command: [type=1, cmd=0x86, ...]
        probe = bytes([1, 0x86, 0, 0, 0, 0]) + bytes(packet_size - 6)

        try:
            write_usb_bulk_packet(self.usb, endpoint=out_ep, payload=probe)
            reply = read_usb_bulk_packet(self.usb, endpoint=in_ep, size=packet_size)
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"GA2-style probe failed: {exc}")

        ascii_preview = bytes(reply).replace(b"\x00", b" ").decode("ascii", errors="ignore").strip()
        return ActionResult(
            True,
            "Probe completed.",
            {
                "out_endpoint": f"0x{out_ep:02x}",
                "in_endpoint": f"0x{in_ep:02x}",
                "reply_hex": bytes(reply).hex(),
                "reply_ascii": ascii_preview,
            },
        )
