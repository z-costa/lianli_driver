from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class TemperatureSensor:
    id: str
    label: str
    source: str
    path: Path

    def read_celsius(self) -> float:
        raw = self.path.read_text(encoding="utf-8").strip()
        value = float(raw)
        if value > 1000:
            return round(value / 1000.0, 2)
        return round(value, 2)

    def as_dict(self, include_value: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "label": self.label,
            "source": self.source,
            "path": str(self.path),
        }
        if include_value:
            try:
                payload["temp_c"] = self.read_celsius()
            except OSError as exc:
                payload["error"] = str(exc)
        return payload


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def discover_temperature_sensors() -> list[TemperatureSensor]:
    sensors: list[TemperatureSensor] = []
    seen_paths: set[str] = set()

    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        path = zone / "temp"
        if not path.exists():
            continue
        zone_type = _safe_read(zone / "type") or zone.name
        zone_key = zone_type.lower().replace(" ", "_")
        sensor = TemperatureSensor(
            id=f"thermal:{zone.name}:{zone_key}",
            label=f"{zone_type} ({zone.name})",
            source="thermal_zone",
            path=path,
        )
        sensors.append(sensor)
        seen_paths.add(str(path))

    for hwmon in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        hwmon_name = _safe_read(hwmon / "name") or hwmon.name
        for temp_input in sorted(hwmon.glob("temp*_input")):
            if str(temp_input) in seen_paths:
                continue
            index = temp_input.stem.replace("temp", "").replace("_input", "")
            label_file = hwmon / f"temp{index}_label"
            label = _safe_read(label_file) or f"{hwmon_name} temp{index}"
            sensor = TemperatureSensor(
                id=f"hwmon:{hwmon.name}:temp{index}",
                label=label,
                source="hwmon",
                path=temp_input,
            )
            sensors.append(sensor)
            seen_paths.add(str(temp_input))

    return sensors
