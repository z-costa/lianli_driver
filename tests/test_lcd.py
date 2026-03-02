from __future__ import annotations

import unittest

from lianli_driver.lcd import build_report_packets, rgb888_to_rgb565, rgb_image_to_rgb565_bytes


class LcdTests(unittest.TestCase):
    def test_rgb565_conversion(self) -> None:
        self.assertEqual(rgb888_to_rgb565(255, 0, 0), 0xF800)
        self.assertEqual(rgb888_to_rgb565(0, 255, 0), 0x07E0)
        self.assertEqual(rgb888_to_rgb565(0, 0, 255), 0x001F)

    def test_rgb_stream_conversion_length(self) -> None:
        # 2 pixels (R + G)
        raw = bytes([255, 0, 0, 0, 255, 0])
        out = rgb_image_to_rgb565_bytes(raw)
        self.assertEqual(len(out), 4)

    def test_build_report_packets(self) -> None:
        payload = bytes(range(120))
        packets = build_report_packets(payload, report_size=64, report_id=0)
        self.assertEqual(len(packets), 2)
        self.assertEqual(len(packets[0]), 64)
        self.assertEqual(len(packets[1]), 64)


if __name__ == "__main__":
    unittest.main()
