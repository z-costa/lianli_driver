from __future__ import annotations

import unittest

from lianli_driver.device_manager import DeviceManager
from lianli_driver.hidraw import HidRawDevice


class DeviceManagerTests(unittest.TestCase):
    def test_is_lian_li_name_variants(self) -> None:
        hid = HidRawDevice(
            path="/dev/hidraw9",
            bus_type=3,
            vendor_id=0x1A86,
            product_id=0x2107,
            name="LIANLI SLV3H",
            accessible=True,
        )
        self.assertTrue(DeviceManager._is_lian_li(None, hid))  # type: ignore[arg-type]

    def test_is_lian_li_by_vendor(self) -> None:
        hid = HidRawDevice(
            path="/dev/hidraw9",
            bus_type=3,
            vendor_id=0x0CF2,
            product_id=0x9999,
            name="Unknown",
            accessible=True,
        )
        self.assertTrue(DeviceManager._is_lian_li(None, hid))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
