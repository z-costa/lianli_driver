# lianli-driver (Ubuntu 24.04)

`lianli-driver` is a Linux-native control stack for Lian Li cooling gear with:

- `hwmon` fan control (manual and temperature-based curves)
- Lian Li USB HID detection (`/dev/hidraw*`)
- Lian Li USB bulk endpoint detection (`usb:<bus>:<dev>`)
- LCD frame pipeline (image -> RGB565 -> HID packets)
- Local web UI (L-Connect style workflow: dashboard + fan curves + LCD upload)

This repo is designed for Ubuntu 24.04 and focuses on real Linux interfaces first.

## Current support status

- Works now:
  - Discover fan PWM channels exposed by Linux (`/sys/class/hwmon`)
  - Apply manual fan speed and auto fan curves by sensor
  - Discover Lian Li HID and USB bulk controllers by VID/PID
  - Build/send LCD packets for devices with a configured transport protocol profile
  - Experimental LCD upload paths for bulk wireless/H2 LCD targets:
    - `wireless_jpg_des` for `1cbe:0005`/`1cbe:0006`
    - `hydroshift_h264_guess` for `1cbe:a021`
- Requires protocol profile:
  - Unknown/new firmware VID:PID pairs not covered by built-ins

Lian Li does not publish Linux protocol documentation for these LCD devices, so this project uses a safe default: it will not issue unknown HID write commands unless you explicitly allow unverified writes.

## Install

```bash
cd /home/ze/lianli_driver
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[full]"
```

If you skip `.[full]`, fan control still works but image loading and USB bulk transfer support may be disabled.

## Run

```bash
source .venv/bin/activate
llctl service --host 127.0.0.1 --port 1787
```

Open:

- <http://127.0.0.1:1787>

## CLI quick commands

```bash
llctl scan
llctl fan-set --channel hwmon2:pwm1 --percent 45
llctl fan-auto --channel hwmon2:pwm1 --sensor hwmon:hwmon1:temp1 --preset balanced
llctl lcd-upload --target /dev/hidraw5 --image /path/to/frame.png --width 480 --height 480 --unsafe-hid-writes
llctl lcd-upload --target usb:001:006 --image /path/to/frame.png --width 400 --height 400 --unsafe-hid-writes
llctl lcd-upload --target usb:001:003 --image /path/to/frame.png --width 480 --height 480 --unsafe-hid-writes
llctl lcd-probe --target usb:001:003
llctl lcd-video --target usb:001:003 --video /path/to/clip.mp4 --width 480 --height 480 --fps 12 --seconds 10 --unsafe-hid-writes
```

## Permissions

Fan, HID, and USB bulk writes usually require elevated privileges unless udev/sysfs permissions are configured.

Example udev rules:

```bash
sudo cp udev/99-lianli-driver.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger
```

If `scan` shows devices with `"accessible": false`, either apply the rule above or run commands with `sudo`.

If `llctl scan` shows temperatures but no `hid_devices`, run:

```bash
ls -l /dev/hidraw*
for d in /sys/class/hidraw/hidraw*; do echo "== $d"; cat "$d/device/uevent"; done
```

The service now uses sysfs fallback (`/sys/class/hidraw/*/device/uevent`) so devices can still be detected even when direct `hidraw` open is blocked.

## HID protocol profile (for LCD writes)

Create `~/.config/lianli-driver/protocols.json` (you can start from `examples/protocols.example.json`):

```json
{
  "0cf2:a102": {
    "name": "UNI FAN TL Controller",
    "transport": "hid",
    "report_size": 64,
    "report_id": 0,
    "lcd": {
      "begin": "A5 5A 01 00",
      "chunk_prefix": "A5 5A 02",
      "end": "A5 5A 03 00",
      "chunk_data_size": 56,
      "include_sequence_le16": true,
      "mode": "framed"
    }
  }
}
```

Hex values above are an example format, not guaranteed for your controller firmware. Replace them with validated bytes from your capture/reverse-engineering session.

Built-in profiles are shipped for known IDs (`1cbe:a021`, `1cbe:0005`, `1cbe:0006`, `0416:8040`, `0416:8041`, `1a86:2107`, `0cf2:a102`).

`wireless_jpg_des` mode uses DES-CBC headers and JPEG payload transfer (inspired by public TL wireless reverse-engineering). HydroShift (`1cbe:a021`) uses `hydroshift_h264_guess`: it runs the wireless sequence and then GA-II Type-B stream fallbacks (single-frame plus short repeated-frame burst; H.264 if `ffmpeg` is available, otherwise JPEG passthrough). Install `pycryptodomex` (`pip install .[full]`) and `ffmpeg` for best results.

## Architecture

- `src/lianli_driver/hwmon.py`: Linux fan channels
- `src/lianli_driver/hidraw.py`: low-level HID enumeration/write
- `src/lianli_driver/usb_bulk.py`: USB bulk enumeration/write
- `src/lianli_driver/fan_curve.py`: curve interpolation + presets
- `src/lianli_driver/service.py`: API + auto-control loop
- `src/lianli_driver/web/*`: web UI

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
