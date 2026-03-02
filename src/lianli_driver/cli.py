from __future__ import annotations

import argparse
import json
import signal
import sys

from .constants import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT
from .service import LianLiService, run_http_service


def _json_print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llctl", description="Lian Li Linux control daemon and CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="Detect hwmon channels, sensors, and Lian Li HID/USB devices.")

    service = sub.add_parser("service", help="Run local web UI and API service.")
    service.add_argument("--host", default=DEFAULT_HTTP_HOST)
    service.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    service.add_argument("--auto-interval", type=float, default=2.0)

    fan_set = sub.add_parser("fan-set", help="Set manual fan speed on a hwmon PWM channel.")
    fan_set.add_argument("--channel", required=True, help="e.g. hwmon2:pwm1")
    fan_set.add_argument("--percent", required=True, type=float)

    fan_auto = sub.add_parser("fan-auto", help="Assign auto fan curve to a PWM channel.")
    fan_auto.add_argument("--channel", required=True)
    fan_auto.add_argument("--sensor", required=True)
    fan_auto.add_argument("--preset", default="balanced", choices=["quiet", "balanced", "performance"])

    fan_auto_off = sub.add_parser("fan-auto-disable", help="Disable auto fan curve assignment.")
    fan_auto_off.add_argument("--channel", required=True)

    lcd = sub.add_parser("lcd-upload", help="Upload image frame to an LCD-capable HID or USB bulk target.")
    lcd.add_argument("--target", required=True, help="e.g. /dev/hidraw5 or usb:001:003")
    lcd.add_argument("--image", required=True)
    lcd.add_argument("--width", type=int, default=480)
    lcd.add_argument("--height", type=int, default=480)
    lcd.add_argument(
        "--unsafe-hid-writes",
        action="store_true",
        help="Allow sending unverified protocol packets to HID/USB targets.",
    )

    probe = sub.add_parser("lcd-probe", help="Probe LCD command channel on USB bulk targets.")
    probe.add_argument("--target", required=True, help="e.g. usb:001:003")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    service = LianLiService(auto_interval_seconds=getattr(args, "auto_interval", 2.0))

    if args.command == "scan":
        _json_print(service.state())
        return 0

    if args.command == "fan-set":
        result = service.set_manual_fan(args.channel, args.percent)
        _json_print(result.to_dict())
        return 0 if result.success else 1

    if args.command == "fan-auto":
        result = service.set_auto_fan(args.channel, args.sensor, preset=args.preset)
        _json_print(result.to_dict())
        return 0 if result.success else 1

    if args.command == "fan-auto-disable":
        result = service.disable_auto_fan(args.channel)
        _json_print(result.to_dict())
        return 0 if result.success else 1

    if args.command == "lcd-upload":
        result = service.upload_lcd_image(
            target_id=args.target,
            image_path=args.image,
            width=args.width,
            height=args.height,
            unsafe_hid_writes=args.unsafe_hid_writes,
        )
        _json_print(result.to_dict())
        return 0 if result.success else 1

    if args.command == "lcd-probe":
        result = service.probe_lcd_target(args.target)
        _json_print(result.to_dict())
        return 0 if result.success else 1

    if args.command == "service":
        def _signal_handler(signum: int, frame: object) -> None:
            del signum, frame
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)
        try:
            run_http_service(service=service, host=args.host, port=args.port)
        except KeyboardInterrupt:
            return 0
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
