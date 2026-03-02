from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .util import ActionResult


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _safe_write(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


@dataclass(slots=True)
class HwmonPwmChannel:
    id: str
    controller: str
    hwmon_name: str
    pwm_index: int
    pwm_path: Path
    pwm_enable_path: Path | None
    fan_input_path: Path | None
    max_pwm: int = 255

    def _try_set_enable(self, candidates: list[int]) -> None:
        if self.pwm_enable_path is None or not self.pwm_enable_path.exists():
            return
        errors: list[str] = []
        for mode in candidates:
            try:
                _safe_write(self.pwm_enable_path, f"{mode}\n")
                return
            except OSError as exc:
                errors.append(str(exc))
        raise OSError("; ".join(errors))

    def set_manual_percent(self, percent: float) -> ActionResult:
        duty = max(0.0, min(100.0, float(percent)))
        pwm_value = int(round((duty / 100.0) * self.max_pwm))
        try:
            self._try_set_enable([1, 0])
            _safe_write(self.pwm_path, f"{pwm_value}\n")
            return ActionResult(
                success=True,
                message=f"Set {self.id} to {duty:.1f}%",
                data={"channel": self.id, "percent": duty, "pwm": pwm_value},
            )
        except OSError as exc:
            return ActionResult(
                success=False,
                message=f"Failed to set {self.id}: {exc}",
                data={"channel": self.id},
            )

    def set_auto_mode(self) -> ActionResult:
        try:
            self._try_set_enable([2])
            return ActionResult(
                success=True,
                message=f"Set {self.id} to firmware auto mode.",
                data={"channel": self.id},
            )
        except OSError as exc:
            return ActionResult(
                success=False,
                message=f"Failed to set auto mode on {self.id}: {exc}",
                data={"channel": self.id},
            )

    def read_percent(self) -> float | None:
        raw = _safe_read(self.pwm_path)
        if not raw:
            return None
        try:
            pwm = int(raw)
        except ValueError:
            return None
        if self.max_pwm <= 0:
            return None
        return round((pwm / self.max_pwm) * 100.0, 1)

    def read_rpm(self) -> int | None:
        if self.fan_input_path is None or not self.fan_input_path.exists():
            return None
        raw = _safe_read(self.fan_input_path)
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "controller": self.controller,
            "hwmon_name": self.hwmon_name,
            "pwm_index": self.pwm_index,
            "pwm_path": str(self.pwm_path),
            "pwm_enable_path": str(self.pwm_enable_path) if self.pwm_enable_path else None,
            "fan_input_path": str(self.fan_input_path) if self.fan_input_path else None,
            "percent": self.read_percent(),
            "rpm": self.read_rpm(),
        }


def discover_pwm_channels() -> list[HwmonPwmChannel]:
    channels: list[HwmonPwmChannel] = []
    for hwmon in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        controller = _safe_read(hwmon / "name") or hwmon.name
        for pwm_path in sorted(hwmon.glob("pwm[0-9]*")):
            if pwm_path.name.endswith("_enable"):
                continue

            try:
                index = int(pwm_path.name.replace("pwm", ""))
            except ValueError:
                continue

            channel = HwmonPwmChannel(
                id=f"{hwmon.name}:pwm{index}",
                controller=controller,
                hwmon_name=hwmon.name,
                pwm_index=index,
                pwm_path=pwm_path,
                pwm_enable_path=(hwmon / f"pwm{index}_enable"),
                fan_input_path=(hwmon / f"fan{index}_input"),
            )
            channels.append(channel)
    return channels
