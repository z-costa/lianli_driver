from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class CurvePoint:
    temp_c: float
    duty_pct: float


def _clamp_percent(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


class FanCurve:
    def __init__(self, points: list[CurvePoint]) -> None:
        if not points:
            raise ValueError("Fan curve requires at least one point.")
        self._points = sorted(points, key=lambda p: p.temp_c)

    @property
    def points(self) -> list[CurvePoint]:
        return list(self._points)

    def duty_for_temp(self, temp_c: float) -> float:
        t = float(temp_c)
        points = self._points
        if t <= points[0].temp_c:
            return _clamp_percent(points[0].duty_pct)
        if t >= points[-1].temp_c:
            return _clamp_percent(points[-1].duty_pct)

        for left, right in zip(points, points[1:], strict=False):
            if left.temp_c <= t <= right.temp_c:
                span = right.temp_c - left.temp_c
                if span == 0:
                    return _clamp_percent(right.duty_pct)
                ratio = (t - left.temp_c) / span
                duty = left.duty_pct + ((right.duty_pct - left.duty_pct) * ratio)
                return _clamp_percent(duty)

        return _clamp_percent(points[-1].duty_pct)

    def as_dict(self) -> dict[str, list[dict[str, float]]]:
        return {
            "points": [
                {"temp_c": p.temp_c, "duty_pct": _clamp_percent(p.duty_pct)}
                for p in self._points
            ]
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "FanCurve":
        raw_points = payload.get("points")
        if not isinstance(raw_points, list):
            raise ValueError("'points' must be a list.")
        points: list[CurvePoint] = []
        for item in raw_points:
            if not isinstance(item, dict):
                raise ValueError("Each point must be an object.")
            temp_c = float(item["temp_c"])
            duty_pct = float(item["duty_pct"])
            points.append(CurvePoint(temp_c=temp_c, duty_pct=duty_pct))
        return cls(points)


PRESET_CURVES: dict[str, FanCurve] = {
    "quiet": FanCurve(
        [
            CurvePoint(30, 20),
            CurvePoint(45, 30),
            CurvePoint(60, 45),
            CurvePoint(75, 70),
            CurvePoint(85, 100),
        ]
    ),
    "balanced": FanCurve(
        [
            CurvePoint(30, 30),
            CurvePoint(45, 45),
            CurvePoint(60, 60),
            CurvePoint(75, 85),
            CurvePoint(85, 100),
        ]
    ),
    "performance": FanCurve(
        [
            CurvePoint(25, 40),
            CurvePoint(40, 60),
            CurvePoint(55, 80),
            CurvePoint(70, 95),
            CurvePoint(80, 100),
        ]
    ),
}
