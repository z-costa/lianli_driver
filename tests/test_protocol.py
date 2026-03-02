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

    def test_builtin_hid_profile_exists(self) -> None:
        registry = ProtocolRegistry(path=None)
        profile = registry.get(0x1A86, 0x2107)
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.transport, "hid")


if __name__ == "__main__":
    unittest.main()
