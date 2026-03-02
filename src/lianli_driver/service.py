from __future__ import annotations

import json
import mimetypes
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .constants import DEFAULT_AUTO_INTERVAL_SECONDS
from .device_manager import DeviceManager
from .fan_curve import FanCurve, PRESET_CURVES
from .lcd import load_image_as_rgb565
from .util import ActionResult


@dataclass(slots=True)
class AutoAssignment:
    channel_id: str
    sensor_id: str
    curve_name: str
    curve: FanCurve

    def as_dict(self) -> dict[str, object]:
        return {
            "channel_id": self.channel_id,
            "sensor_id": self.sensor_id,
            "curve_name": self.curve_name,
            "curve": self.curve.as_dict(),
        }


class LianLiService:
    def __init__(self, auto_interval_seconds: float = DEFAULT_AUTO_INTERVAL_SECONDS) -> None:
        self.auto_interval_seconds = float(auto_interval_seconds)
        self.manager = DeviceManager()
        self.auto_assignments: dict[str, AutoAssignment] = {}
        self.last_auto_results: dict[str, dict[str, object]] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._auto_thread: threading.Thread | None = None

    def refresh(self) -> dict[str, object]:
        with self._lock:
            self.manager.protocols.reload()
            snapshot = self.manager.refresh()
            return snapshot.as_dict()

    def state(self) -> dict[str, object]:
        with self._lock:
            snapshot = self.manager.snapshot
            return {
                "snapshot": snapshot.as_dict(),
                "auto_assignments": {
                    channel_id: assignment.as_dict()
                    for channel_id, assignment in self.auto_assignments.items()
                },
                "last_auto_results": self.last_auto_results,
                "curve_presets": sorted(PRESET_CURVES.keys()),
            }

    def set_manual_fan(self, channel_id: str, percent: float) -> ActionResult:
        with self._lock:
            channel = self.manager.find_pwm_channel(channel_id)
            if channel is None:
                return ActionResult(False, f"Unknown channel: {channel_id}")
            result = channel.set_manual_percent(percent)
            if result.success:
                self.auto_assignments.pop(channel_id, None)
            return result

    def set_auto_fan(
        self,
        channel_id: str,
        sensor_id: str,
        preset: str | None = None,
        custom_curve: FanCurve | None = None,
    ) -> ActionResult:
        with self._lock:
            channel = self.manager.find_pwm_channel(channel_id)
            if channel is None:
                return ActionResult(False, f"Unknown channel: {channel_id}")
            sensor = self.manager.find_sensor(sensor_id)
            if sensor is None:
                return ActionResult(False, f"Unknown sensor: {sensor_id}")
            curve_name = "custom"
            curve = custom_curve
            if curve is None:
                if not preset:
                    return ActionResult(False, "Missing preset for auto mode.")
                selected = PRESET_CURVES.get(preset.lower())
                if selected is None:
                    return ActionResult(False, f"Unknown preset: {preset}")
                curve = selected
                curve_name = preset.lower()

            assert curve is not None
            self.auto_assignments[channel_id] = AutoAssignment(
                channel_id=channel_id,
                sensor_id=sensor_id,
                curve_name=curve_name,
                curve=curve,
            )
            result = self._apply_auto_for(channel_id)
            return result

    def disable_auto_fan(self, channel_id: str) -> ActionResult:
        with self._lock:
            if channel_id in self.auto_assignments:
                self.auto_assignments.pop(channel_id, None)
                self.last_auto_results.pop(channel_id, None)
                return ActionResult(True, f"Disabled auto control on {channel_id}.")
            return ActionResult(False, f"No auto profile assigned to {channel_id}.")

    def upload_lcd_image(
        self,
        target_id: str,
        image_path: str,
        width: int,
        height: int,
        unsafe_hid_writes: bool = False,
    ) -> ActionResult:
        with self._lock:
            target = target_id.strip()
            if not target:
                return ActionResult(False, "Missing LCD target.")
            try:
                frame = load_image_as_rgb565(image_path, width, height)
            except Exception as exc:  # noqa: BLE001
                return ActionResult(False, f"Failed to load image: {exc}")

            if target.startswith("/dev/hidraw"):
                device = self.manager.find_hid_device(target)
                if device is None:
                    return ActionResult(False, f"Unknown HID device: {target}")
                return device.upload_lcd_rgb565(frame, unsafe_hid_writes=unsafe_hid_writes)

            if target.startswith("usb:"):
                bulk_device = self.manager.find_bulk_device(target)
                if bulk_device is None:
                    return ActionResult(False, f"Unknown USB bulk device: {target}")
                return bulk_device.upload_lcd_rgb565(frame, unsafe_hid_writes=unsafe_hid_writes)

            # Legacy client fallback: try as hidraw first.
            hid_device = self.manager.find_hid_device(target)
            if hid_device is not None:
                return hid_device.upload_lcd_rgb565(frame, unsafe_hid_writes=unsafe_hid_writes)
            bulk_device = self.manager.find_bulk_device(target)
            if bulk_device is not None:
                return bulk_device.upload_lcd_rgb565(frame, unsafe_hid_writes=unsafe_hid_writes)
            return ActionResult(False, f"Unknown LCD target: {target}")

    def probe_lcd_target(self, target_id: str) -> ActionResult:
        with self._lock:
            target = target_id.strip()
            if target.startswith("usb:"):
                bulk_device = self.manager.find_bulk_device(target)
                if bulk_device is None:
                    return ActionResult(False, f"Unknown USB bulk device: {target}")
                return bulk_device.probe_ga2_style_channel()
            return ActionResult(
                False,
                "Probe currently supports only usb:* targets (bulk endpoints).",
            )

    def start_auto_loop(self) -> None:
        if self._auto_thread and self._auto_thread.is_alive():
            return
        self._stop_event.clear()
        self._auto_thread = threading.Thread(target=self._auto_loop, daemon=True, name="auto-control")
        self._auto_thread.start()

    def stop_auto_loop(self) -> None:
        self._stop_event.set()
        thread = self._auto_thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def _auto_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                for channel_id in list(self.auto_assignments.keys()):
                    self._apply_auto_for(channel_id)
            self._stop_event.wait(self.auto_interval_seconds)

    def _apply_auto_for(self, channel_id: str) -> ActionResult:
        assignment = self.auto_assignments.get(channel_id)
        if assignment is None:
            return ActionResult(False, f"No assignment for {channel_id}.")

        channel = self.manager.find_pwm_channel(channel_id)
        if channel is None:
            return ActionResult(False, f"Channel disappeared: {channel_id}")
        sensor = self.manager.find_sensor(assignment.sensor_id)
        if sensor is None:
            return ActionResult(False, f"Sensor disappeared: {assignment.sensor_id}")

        try:
            temp_c = sensor.read_celsius()
        except OSError as exc:
            return ActionResult(False, f"Failed to read sensor {sensor.id}: {exc}")

        duty = assignment.curve.duty_for_temp(temp_c)
        result = channel.set_manual_percent(duty)
        self.last_auto_results[channel_id] = {
            "sensor_id": sensor.id,
            "temp_c": temp_c,
            "duty_pct": duty,
            "result": result.to_dict(),
            "timestamp": time.time(),
        }
        return result


class ApiHandler(BaseHTTPRequestHandler):
    service: LianLiService
    web_root: Path

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._respond_json({"ok": True, "state": self.service.state()})
            return
        if parsed.path == "/api/refresh":
            self._respond_json({"ok": True, "snapshot": self.service.refresh()})
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        body = self._read_json_body()
        if body is None:
            return

        if parsed.path == "/api/refresh":
            payload = self.service.refresh()
            self._respond_json({"ok": True, "snapshot": payload})
            return

        if parsed.path == "/api/fans/manual":
            channel_id = str(body.get("channel_id", ""))
            percent = float(body.get("percent", 0))
            result = self.service.set_manual_fan(channel_id, percent)
            self._respond_action(result)
            return

        if parsed.path == "/api/fans/auto":
            channel_id = str(body.get("channel_id", ""))
            sensor_id = str(body.get("sensor_id", ""))
            preset = body.get("preset")
            curve_payload = body.get("curve")
            custom_curve = None
            if isinstance(curve_payload, dict):
                try:
                    custom_curve = FanCurve.from_dict(curve_payload)
                except Exception as exc:  # noqa: BLE001
                    self._respond_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
            result = self.service.set_auto_fan(
                channel_id=channel_id,
                sensor_id=sensor_id,
                preset=str(preset) if preset is not None else None,
                custom_curve=custom_curve,
            )
            self._respond_action(result)
            return

        if parsed.path == "/api/fans/auto/disable":
            channel_id = str(body.get("channel_id", ""))
            result = self.service.disable_auto_fan(channel_id)
            self._respond_action(result)
            return

        if parsed.path == "/api/lcd/upload":
            target_id = str(body.get("target_id", body.get("hidraw_path", "")))
            image_path = str(body.get("image_path", ""))
            width = int(body.get("width", 480))
            height = int(body.get("height", 480))
            unsafe = bool(body.get("unsafe_hid_writes", False))
            result = self.service.upload_lcd_image(
                target_id=target_id,
                image_path=image_path,
                width=width,
                height=height,
                unsafe_hid_writes=unsafe,
            )
            self._respond_action(result)
            return

        if parsed.path == "/api/lcd/probe":
            target_id = str(body.get("target_id", ""))
            result = self.service.probe_lcd_target(target_id=target_id)
            self._respond_action(result)
            return

        self._respond_json({"ok": False, "error": f"Unknown endpoint: {parsed.path}"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _read_json_body(self) -> dict[str, object] | None:
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError:
            length = 0
        payload = self.rfile.read(length) if length > 0 else b"{}"
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._respond_json({"ok": False, "error": f"Invalid JSON: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(parsed, dict):
            self._respond_json({"ok": False, "error": "JSON body must be an object."}, status=HTTPStatus.BAD_REQUEST)
            return None
        return parsed

    def _respond_action(self, result: ActionResult) -> None:
        status = HTTPStatus.OK if result.success else HTTPStatus.BAD_REQUEST
        self._respond_json({"ok": result.success, "result": result.to_dict()}, status=status)

    def _respond_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, raw_path: str) -> None:
        path = raw_path
        if path in {"", "/"}:
            path = "/index.html"
        normalized = Path(path.lstrip("/"))
        target = (self.web_root / normalized).resolve()

        if self.web_root not in target.parents and target != self.web_root:
            self.send_error(HTTPStatus.FORBIDDEN.value)
            return
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND.value)
            return

        content = target.read_bytes()
        content_type, _ = mimetypes.guess_type(str(target))
        if not content_type:
            content_type = "application/octet-stream"
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def run_http_service(
    service: LianLiService,
    host: str,
    port: int,
    web_root: Path | None = None,
) -> None:
    if web_root is None:
        web_root = Path(__file__).with_name("web")
    web_root = web_root.resolve()

    class BoundApiHandler(ApiHandler):
        pass

    BoundApiHandler.service = service
    BoundApiHandler.web_root = web_root
    server = ThreadingHTTPServer((host, int(port)), BoundApiHandler)
    service.start_auto_loop()
    try:
        server.serve_forever()
    finally:
        service.stop_auto_loop()
        server.server_close()
