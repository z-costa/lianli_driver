from __future__ import annotations

from dataclasses import dataclass

from .constants import KNOWN_USB_PRODUCTS, LIAN_LI_RELATED_USB_VENDOR_IDS, LIAN_LI_VENDOR_IDS
from .devices import (
    HydroShiftIILcdDevice,
    LianLiUsbBulkDevice,
    LianLiUsbDevice,
    UniFanTlController,
)
from .hidraw import HidRawDevice, enumerate_hidraw
from .hwmon import HwmonPwmChannel, discover_pwm_channels
from .protocol import ProtocolRegistry
from .sensors import TemperatureSensor, discover_temperature_sensors
from .usb_bulk import UsbBulkDevice, discover_usb_bulk_devices


@dataclass(slots=True)
class Snapshot:
    hid_devices: list[LianLiUsbDevice]
    bulk_devices: list[LianLiUsbBulkDevice]
    pwm_channels: list[HwmonPwmChannel]
    sensors: list[TemperatureSensor]

    def as_dict(self) -> dict[str, object]:
        return {
            "hid_devices": [d.as_dict() for d in self.hid_devices],
            "bulk_devices": [d.as_dict() for d in self.bulk_devices],
            "pwm_channels": [c.as_dict() for c in self.pwm_channels],
            "sensors": [s.as_dict() for s in self.sensors],
        }


class DeviceManager:
    def __init__(self, protocols: ProtocolRegistry | None = None) -> None:
        self.protocols = protocols or ProtocolRegistry()
        self.snapshot = Snapshot(hid_devices=[], bulk_devices=[], pwm_channels=[], sensors=[])
        self.refresh()

    def refresh(self) -> Snapshot:
        hidraw_devices = enumerate_hidraw(vendor_filter=None)
        usb_devices: list[LianLiUsbDevice] = []
        for hid in hidraw_devices:
            if not self._is_lian_li(hid):
                continue
            usb_devices.append(self._make_usb_device(hid))

        bulk_usb_devices = discover_usb_bulk_devices()
        bulk_devices: list[LianLiUsbBulkDevice] = []
        for usb in bulk_usb_devices:
            if not self._is_lian_li_bulk(usb):
                continue
            bulk_devices.append(self._make_bulk_device(usb))

        self.snapshot = Snapshot(
            hid_devices=usb_devices,
            bulk_devices=bulk_devices,
            pwm_channels=discover_pwm_channels(),
            sensors=discover_temperature_sensors(),
        )
        return self.snapshot

    def find_pwm_channel(self, channel_id: str) -> HwmonPwmChannel | None:
        for channel in self.snapshot.pwm_channels:
            if channel.id == channel_id:
                return channel
        return None

    def find_sensor(self, sensor_id: str) -> TemperatureSensor | None:
        for sensor in self.snapshot.sensors:
            if sensor.id == sensor_id:
                return sensor
        return None

    def find_hid_device(self, hidraw_path: str) -> LianLiUsbDevice | None:
        for device in self.snapshot.hid_devices:
            if device.hid.path == hidraw_path:
                return device
        return None

    def find_bulk_device(self, bulk_id: str) -> LianLiUsbBulkDevice | None:
        for device in self.snapshot.bulk_devices:
            if device.usb.id == bulk_id:
                return device
        return None

    def _is_lian_li(self, hid: HidRawDevice) -> bool:
        if hid.vendor_id in LIAN_LI_VENDOR_IDS:
            return True
        upper_name = hid.name.upper()
        return (
            "LIAN LI" in upper_name
            or "LIAN-LI" in upper_name
            or "LIANLI" in upper_name
        )

    def _is_lian_li_bulk(self, usb: UsbBulkDevice) -> bool:
        if usb.vendor_id in LIAN_LI_RELATED_USB_VENDOR_IDS:
            return True
        merged = f"{usb.manufacturer} {usb.product}".upper()
        return (
            "LIANLI" in merged
            or "LIAN LI" in merged
            or "HYDROSHIFT" in merged
            or "SL-LCD" in merged
            or "SLV3" in merged
        )

    def _make_usb_device(self, hid: HidRawDevice) -> LianLiUsbDevice:
        product_name = KNOWN_USB_PRODUCTS.get(hid.product_id, hid.name or f"Lian Li USB Device ({hid.key})")
        protocol = self.protocols.get(hid.vendor_id, hid.product_id)
        capabilities: set[str] = set()
        upper_name = f"{product_name} {hid.name}".upper()
        if "LCD" in upper_name:
            capabilities.add("lcd")
        if protocol is not None and protocol.lcd is not None:
            capabilities.add("lcd")

        if hid.product_id == 0xA102:
            return UniFanTlController(
                hid=hid,
                model=product_name,
                capabilities=capabilities,
                protocol=protocol,
            )
        if hid.product_id == 0xA200:
            return HydroShiftIILcdDevice(
                hid=hid,
                model=product_name,
                capabilities=capabilities,
                protocol=protocol,
            )
        return LianLiUsbDevice(
            hid=hid,
            model=product_name,
            capabilities=capabilities,
            protocol=protocol,
        )

    def _make_bulk_device(self, usb: UsbBulkDevice) -> LianLiUsbBulkDevice:
        model = KNOWN_USB_PRODUCTS.get(usb.product_id, usb.product or f"Lian Li USB Device ({usb.key})")
        protocol = self.protocols.get(usb.vendor_id, usb.product_id)
        capabilities: set[str] = set()
        upper_name = f"{model} {usb.manufacturer} {usb.product}".upper()
        if "LCD" in upper_name or "H2" in upper_name or "HYDROSHIFT" in upper_name:
            capabilities.add("lcd")
        if protocol is not None and protocol.lcd is not None:
            capabilities.add("lcd")
        return LianLiUsbBulkDevice(
            usb=usb,
            model=model,
            capabilities=capabilities,
            protocol=protocol,
        )
