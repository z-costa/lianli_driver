from __future__ import annotations

import fcntl
import os
import struct
from dataclasses import dataclass
from pathlib import Path


IOC_NRBITS = 8
IOC_TYPEBITS = 8
IOC_SIZEBITS = 14
IOC_DIRBITS = 2

IOC_NRSHIFT = 0
IOC_TYPESHIFT = IOC_NRSHIFT + IOC_NRBITS
IOC_SIZESHIFT = IOC_TYPESHIFT + IOC_TYPEBITS
IOC_DIRSHIFT = IOC_SIZESHIFT + IOC_SIZEBITS

IOC_WRITE = 1
IOC_READ = 2


def _IOC(direction: int, ioctl_type: int, nr: int, size: int) -> int:
    return (
        (direction << IOC_DIRSHIFT)
        | (ioctl_type << IOC_TYPESHIFT)
        | (nr << IOC_NRSHIFT)
        | (size << IOC_SIZESHIFT)
    )


def _IOR(ioctl_type: int, nr: int, size: int) -> int:
    return _IOC(IOC_READ, ioctl_type, nr, size)


_HIDRAW_INFO_STRUCT = struct.Struct("IHH")
HIDIOCGRAWINFO = _IOR(ord("H"), 0x03, _HIDRAW_INFO_STRUCT.size)


def HIDIOCGRAWNAME(length: int) -> int:
    return _IOC(IOC_READ, ord("H"), 0x04, length)


@dataclass(slots=True)
class HidRawDevice:
    path: str
    bus_type: int
    vendor_id: int
    product_id: int
    name: str
    accessible: bool
    error: str | None = None

    @property
    def key(self) -> str:
        return f"{self.vendor_id:04x}:{self.product_id:04x}"

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "bus_type": self.bus_type,
            "vendor_id": f"0x{self.vendor_id:04x}",
            "product_id": f"0x{self.product_id:04x}",
            "name": self.name,
            "accessible": self.accessible,
            "error": self.error,
            "key": self.key,
        }


def _ioctl_read_name(fd: int, buffer_size: int = 256) -> str:
    raw = bytearray(buffer_size)
    fcntl.ioctl(fd, HIDIOCGRAWNAME(buffer_size), raw, True)
    return bytes(raw).split(b"\x00", maxsplit=1)[0].decode("utf-8", errors="replace")


def _ioctl_read_info(fd: int) -> tuple[int, int, int]:
    raw = bytearray(_HIDRAW_INFO_STRUCT.size)
    fcntl.ioctl(fd, HIDIOCGRAWINFO, raw, True)
    bus, vendor, product = _HIDRAW_INFO_STRUCT.unpack(raw)
    return bus, vendor, product


def _parse_hid_id(raw_hid_id: str) -> tuple[int, int, int] | None:
    # Expected format: "0003:00000CF2:0000A102"
    parts = raw_hid_id.split(":")
    if len(parts) != 3:
        return None
    try:
        bus = int(parts[0], 16)
        vendor = int(parts[1], 16) & 0xFFFF
        product = int(parts[2], 16) & 0xFFFF
    except ValueError:
        return None
    return bus, vendor, product


def _read_sysfs_hidraw_metadata(dev_path: Path) -> tuple[int, int, int, str] | None:
    hidraw_name = dev_path.name
    uevent_path = Path("/sys/class/hidraw") / hidraw_name / "device" / "uevent"
    try:
        lines = uevent_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    fields: dict[str, str] = {}
    for line in lines:
        if "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        fields[key] = value

    hid_id = _parse_hid_id(fields.get("HID_ID", ""))
    if hid_id is None:
        return None
    bus, vendor, product = hid_id
    name = fields.get("HID_NAME", hidraw_name)
    return bus, vendor, product, name


def enumerate_hidraw(vendor_filter: set[int] | None = None) -> list[HidRawDevice]:
    devices: list[HidRawDevice] = []
    for path in sorted(Path("/dev").glob("hidraw*")):
        sysfs_meta = _read_sysfs_hidraw_metadata(path)
        fd = None
        bus = 0
        vendor = 0
        product = 0
        name = path.name
        accessible = False
        error: str | None = None
        try:
            fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK)
            bus, vendor, product = _ioctl_read_info(fd)
            name = _ioctl_read_name(fd) or name
            accessible = True
        except OSError as exc:
            error = str(exc)
            if sysfs_meta is not None:
                bus, vendor, product, name = sysfs_meta
        else:
            if sysfs_meta is not None:
                # Keep ioctl values authoritative; only fill missing values from sysfs.
                sys_bus, sys_vendor, sys_product, sys_name = sysfs_meta
                bus = bus or sys_bus
                vendor = vendor or sys_vendor
                product = product or sys_product
                name = name or sys_name
        finally:
            if fd is not None:
                os.close(fd)

        if vendor_filter and vendor not in vendor_filter:
            continue
        devices.append(
            HidRawDevice(
                path=str(path),
                bus_type=bus,
                vendor_id=vendor,
                product_id=product,
                name=name,
                accessible=accessible,
                error=error,
            )
        )
    return devices


def write_hid_report(path: str, report: bytes) -> int:
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    try:
        return os.write(fd, report)
    finally:
        os.close(fd)
