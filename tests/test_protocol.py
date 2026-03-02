from __future__ import annotations

import unittest

from lianli_driver.protocol import ProtocolRegistry


class ProtocolTests(unittest.TestCase):
    def test_builtin_bulk_profile_exists(self) -> None:
        registry = ProtocolRegistry(path=None)
        profile = registry.get(0x1CBE, 0xA021)
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.transport, "usb_bulk")
        self.assertIsNotNone(profile.lcd)
        assert profile.lcd is not None
        self.assertEqual(profile.lcd.mode, "hydroshift_h264_guess")

    def test_builtin_hid_profile_exists(self) -> None:
        registry = ProtocolRegistry(path=None)
        profile = registry.get(0x1A86, 0x2107)
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.transport, "hid")

    def test_sl_lcd_wireless_profile_has_lcd_mode(self) -> None:
        registry = ProtocolRegistry(path=None)
        profile = registry.get(0x1CBE, 0x0005)
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.transport, "usb_bulk")
        self.assertIsNotNone(profile.lcd)
        assert profile.lcd is not None
        self.assertEqual(profile.lcd.mode, "wireless_jpg_des")


if __name__ == "__main__":
    unittest.main()
