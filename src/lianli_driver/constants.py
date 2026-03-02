from __future__ import annotations

from pathlib import Path

APP_NAME = "lianli-driver"
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 1787
DEFAULT_AUTO_INTERVAL_SECONDS = 2.0

# USB IDs gathered from public Linux ecosystem references/issues.
LIAN_LI_VENDOR_IDS = {0x0CF2}
LIAN_LI_RELATED_USB_VENDOR_IDS = {0x0CF2, 0x1CBE, 0x0416, 0x1A86, 0x04FC}
KNOWN_USB_PRODUCTS = {
    0x0005: "Lian Li SL-LCD Wireless-1.2",
    0x0006: "Lian Li TL-LCD Wireless-1.3",
    0xA100: "Lian Li Controller",
    0xA101: "Lian Li UNI FAN Controller",
    0xA102: "Lian Li UNI FAN TL Controller",
    0xA021: "Lian Li HydroShift II (lianli-H2-1.0)",
    0xA200: "Lian Li HydroShift II LCD-C (unverified)",
    0x2107: "LIANLI SLV3H Controller (bridge)",
    0x7393: "Lian Li TL LCD",
    0x8040: "Lian Li SLV3TX Wireless TX",
    0x8041: "Lian Li SLV3RX Wireless RX",
}

DEFAULT_CONFIG_DIR = Path.home() / ".config" / APP_NAME
DEFAULT_PROTOCOL_FILE = DEFAULT_CONFIG_DIR / "protocols.json"
