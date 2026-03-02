from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .constants import LIAN_LI_RELATED_USB_VENDOR_IDS


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _parse_hex(text: str) -> int:
    try:
        return int(text, 16)
    except ValueError:
        return 0


@dataclass(slots=True)
class UsbEndpoint:
    address: int
    direction: str
    transfer_type: str
    max_packet_size: int

    def as_dict(self) -> dict[str, object]:
        return {
            "address": f"0x{self.address:02x}",
            "direction": self.direction,
            "transfer_type": self.transfer_type,
            "max_packet_size": self.max_packet_size,
        }


@dataclass(slots=True)
class UsbBulkDevice:
    id: str
    sysfs_name: str
    bus_num: int
    dev_num: int
    vendor_id: int
    product_id: int
    manufacturer: str
    product: str
    serial: str
    dev_node: str
    endpoints: list[UsbEndpoint]
    accessible: bool
    error: str | None = None

    @property
    def key(self) -> str:
        return f"{self.vendor_id:04x}:{self.product_id:04x}"

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "sysfs_name": self.sysfs_name,
            "bus_num": self.bus_num,
            "dev_num": self.dev_num,
            "vendor_id": f"0x{self.vendor_id:04x}",
            "product_id": f"0x{self.product_id:04x}",
            "manufacturer": self.manufacturer,
            "product": self.product,
            "serial": self.serial,
            "dev_node": self.dev_node,
            "endpoints": [ep.as_dict() for ep in self.endpoints],
            "accessible": self.accessible,
            "error": self.error,
            "key": self.key,
        }


def _discover_endpoints(usb_device_path: Path) -> list[UsbEndpoint]:
    endpoints: list[UsbEndpoint] = []
    for iface in sorted(usb_device_path.parent.glob(f"{usb_device_path.name}:*")):
        for ep_path in sorted(iface.glob("ep_*")):
            addr_text = _safe_read(ep_path / "bEndpointAddress")
            transfer_type = _safe_read(ep_path / "type")
            direction = _safe_read(ep_path / "direction")
            packet_size_text = _safe_read(ep_path / "wMaxPacketSize")
            if not addr_text:
                continue
            endpoint = UsbEndpoint(
                address=_parse_hex(addr_text),
                direction=direction.lower() or "unknown",
                transfer_type=transfer_type.lower() or "unknown",
                max_packet_size=_parse_hex(packet_size_text) if packet_size_text else 0,
            )
            endpoints.append(endpoint)
    return endpoints


def _is_lian_li_related(vendor_id: int, manufacturer: str, product: str) -> bool:
    if vendor_id in LIAN_LI_RELATED_USB_VENDOR_IDS:
        return True
    merged = f"{manufacturer} {product}".upper()
    for token in ("LIANLI", "LIAN LI", "SL-LCD", "HYDROSHIFT", "GALLAHAD", "SLV3"):
        if token in merged:
            return True
    return False


def discover_usb_bulk_devices() -> list[UsbBulkDevice]:
    devices: list[UsbBulkDevice] = []
    usb_root = Path("/sys/bus/usb/devices")
    for path in sorted(usb_root.glob("*")):
        if not (path / "idVendor").exists() or not (path / "idProduct").exists():
            continue

        vendor_id = _parse_hex(_safe_read(path / "idVendor"))
        product_id = _parse_hex(_safe_read(path / "idProduct"))
        manufacturer = _safe_read(path / "manufacturer")
        product = _safe_read(path / "product")
        serial = _safe_read(path / "serial")

        if not _is_lian_li_related(vendor_id, manufacturer, product):
            continue

        bus_num_text = _safe_read(path / "busnum")
        dev_num_text = _safe_read(path / "devnum")
        if not bus_num_text or not dev_num_text:
            continue
        bus_num = int(bus_num_text)
        dev_num = int(dev_num_text)

        endpoints = _discover_endpoints(path)
        has_bulk = any(ep.transfer_type == "bulk" for ep in endpoints)
        if not has_bulk:
            continue

        dev_node = f"/dev/bus/usb/{bus_num:03d}/{dev_num:03d}"
        accessible = os.access(dev_node, os.R_OK | os.W_OK)
        error = None if accessible else "permission denied"

        device_id = f"usb:{bus_num:03d}:{dev_num:03d}"
        devices.append(
            UsbBulkDevice(
                id=device_id,
                sysfs_name=path.name,
                bus_num=bus_num,
                dev_num=dev_num,
                vendor_id=vendor_id,
                product_id=product_id,
                manufacturer=manufacturer,
                product=product,
                serial=serial,
                dev_node=dev_node,
                endpoints=endpoints,
                accessible=accessible,
                error=error,
            )
        )
    return devices


def _require_pyusb() -> tuple[object, object]:
    try:
        import usb.core  # type: ignore[import-not-found]
        import usb.util  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("pyusb is required for USB bulk writes (pip install pyusb).") from exc
    return usb.core, usb.util


def write_usb_bulk_packet(
    device: UsbBulkDevice,
    endpoint: int,
    payload: bytes,
    timeout_ms: int = 2000,
) -> int:
    usb_core, usb_util = _require_pyusb()
    dev = usb_core.find(
        idVendor=device.vendor_id,
        idProduct=device.product_id,
        bus=device.bus_num,
        address=device.dev_num,
    )
    if dev is None:
        raise RuntimeError(
            f"USB device not found on bus {device.bus_num:03d} address {device.dev_num:03d}."
        )

    if dev.is_kernel_driver_active(0):  # pragma: no cover - env dependent
        dev.detach_kernel_driver(0)
    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    usb_util.claim_interface(dev, intf.bInterfaceNumber)
    try:
        written = dev.write(endpoint, payload, timeout=timeout_ms)
        return int(written)
    finally:
        usb_util.release_interface(dev, intf.bInterfaceNumber)
        usb_util.dispose_resources(dev)


def read_usb_bulk_packet(
    device: UsbBulkDevice,
    endpoint: int,
    size: int,
    timeout_ms: int = 2000,
) -> bytes:
    usb_core, usb_util = _require_pyusb()
    dev = usb_core.find(
        idVendor=device.vendor_id,
        idProduct=device.product_id,
        bus=device.bus_num,
        address=device.dev_num,
    )
    if dev is None:
        raise RuntimeError(
            f"USB device not found on bus {device.bus_num:03d} address {device.dev_num:03d}."
        )

    if dev.is_kernel_driver_active(0):  # pragma: no cover - env dependent
        dev.detach_kernel_driver(0)
    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    usb_util.claim_interface(dev, intf.bInterfaceNumber)
    try:
        data = dev.read(endpoint, size, timeout=timeout_ms)
        return bytes(data)
    finally:
        usb_util.release_interface(dev, intf.bInterfaceNumber)
        usb_util.dispose_resources(dev)
