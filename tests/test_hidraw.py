from __future__ import annotations

import unittest

from lianli_driver.hidraw import _parse_hid_id


class HidrawTests(unittest.TestCase):
    def test_parse_hid_id(self) -> None:
        parsed = _parse_hid_id("0003:00000CF2:0000A102")
        self.assertEqual(parsed, (0x0003, 0x0CF2, 0xA102))

    def test_parse_hid_id_invalid(self) -> None:
        self.assertIsNone(_parse_hid_id("bad"))
        self.assertIsNone(_parse_hid_id("0003:zzz:0000A102"))


if __name__ == "__main__":
    unittest.main()
