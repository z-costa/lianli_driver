from __future__ import annotations

import unittest

from lianli_driver.fan_curve import CurvePoint, FanCurve


class FanCurveTests(unittest.TestCase):
    def test_interpolates_between_points(self) -> None:
        curve = FanCurve([CurvePoint(30, 20), CurvePoint(70, 80)])
        self.assertAlmostEqual(curve.duty_for_temp(50), 50.0)

    def test_clamps_low_and_high(self) -> None:
        curve = FanCurve([CurvePoint(30, 20), CurvePoint(70, 80)])
        self.assertEqual(curve.duty_for_temp(20), 20.0)
        self.assertEqual(curve.duty_for_temp(90), 80.0)

    def test_rejects_empty_curve(self) -> None:
        with self.assertRaises(ValueError):
            FanCurve([])


if __name__ == "__main__":
    unittest.main()
