from __future__ import annotations

import shutil
import subprocess
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field

from ..lcd import chunk_bytes
from ..protocol import HidProtocolProfile
from ..usb_bulk import UsbBulkDevice, read_usb_bulk_packet, write_usb_bulk_packet
from ..util import ActionResult

_WIRELESS_KEY = b"slv3tuzx"
_WIRELESS_HEADER_SIZE = 512
_WIRELESS_INNER_HEADER_SIZE = 504
_WIRELESS_PACKET_SIZE = 102400
_WIRELESS_MAX_PAYLOAD = _WIRELESS_PACKET_SIZE - _WIRELESS_HEADER_SIZE
_WIRELESS_CMD_GET_VER = 10
_WIRELESS_CMD_REBOOT = 11
_WIRELESS_CMD_ROTATE = 13
_WIRELESS_CMD_BRIGHTNESS = 14
_WIRELESS_CMD_SET_FRAME_RATE = 15
_WIRELESS_CMD_STOP_CLOCK = 41
_WIRELESS_CMD_PUSH_JPG = 101
_WIRELESS_CMD_START_PLAY = 121
_WIRELESS_CMD_QUERY_BLOCK = 122
_WIRELESS_CMD_STOP_PLAY = 123
_WIRELESS_CMD_GET_POS_INDEX = 201
_HYDROSHIFT_KEY = "1cbe:a021"
_GA2_TYPE_A = 0x01
_GA2_TYPE_B = 0x02
_GA2_TYPE_B_HEADER_SIZE = 11
_GA2_TYPE_B_CMD_CANDIDATES = (0x41, 0x01, 0x00, 0x61)
_GA2_STREAM_BURST_COMMANDS = (0x41, 0x61, 0x46, 0x48)


def _pkcs7_pad(payload: bytes, block_size: int = 8) -> bytes:
    pad_len = block_size - (len(payload) % block_size)
    return payload + bytes([pad_len] * pad_len)


def _require_des_cipher() -> object:
    try:
        from Cryptodome.Cipher import DES  # type: ignore[import-not-found]
    except ImportError:
        try:
            from Crypto.Cipher import DES  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "pycryptodomex is required for encrypted wireless LCD uploads "
                "(install with: pip install pycryptodomex).",
            ) from exc
    return DES


def _wireless_timestamp_ms() -> int:
    now = datetime.utcnow()
    epoch = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    return int((now - epoch).total_seconds() * 1000) & 0xFFFFFFFF


def _wireless_encrypt(payload: bytes) -> bytes:
    des = _require_des_cipher()
    cipher = des.new(_WIRELESS_KEY, des.MODE_CBC, iv=_WIRELESS_KEY)
    return cipher.encrypt(_pkcs7_pad(payload, 8))


def _build_wireless_packet(
    command: int,
    payload: bytes | None = None,
    single_byte: int | None = None,
) -> bytes:
    header = bytearray(_WIRELESS_INNER_HEADER_SIZE)
    header[0] = command & 0xFF
    header[2] = 26
    header[3] = 109
    header[4:8] = _wireless_timestamp_ms().to_bytes(4, "little", signed=False)

    if payload is not None:
        if len(payload) > _WIRELESS_MAX_PAYLOAD:
            raise RuntimeError(
                f"JPEG payload too large for wireless transfer: {len(payload)} > {_WIRELESS_MAX_PAYLOAD}"
            )
        header[8:12] = len(payload).to_bytes(4, "big", signed=False)
    elif single_byte is not None:
        header[8] = single_byte & 0xFF

    encrypted = _wireless_encrypt(bytes(header))
    if payload is None:
        packet = bytearray(_WIRELESS_HEADER_SIZE)
        packet[: len(encrypted)] = encrypted
        return bytes(packet)

    packet_len = max(_WIRELESS_PACKET_SIZE, _WIRELESS_HEADER_SIZE + len(payload))
    packet = bytearray(packet_len)
    packet[: len(encrypted)] = encrypted
    packet[_WIRELESS_HEADER_SIZE : _WIRELESS_HEADER_SIZE + len(payload)] = payload
    return bytes(packet)


def _build_ga2_type_a_packet(command: int, packet_size: int, payload: bytes = b"") -> bytes:
    size = max(64, int(packet_size))
    chunk = payload[:58]
    packet = bytearray(size)
    packet[0] = _GA2_TYPE_A
    packet[1] = command & 0xFF
    packet[2] = 0
    packet[3] = 0
    packet[4] = 0
    packet[5] = len(chunk) & 0xFF
    packet[6 : 6 + len(chunk)] = chunk
    return bytes(packet)


def _build_ga2_type_b_packets(payload: bytes, command: int, packet_size: int) -> list[bytes]:
    size = max(64, int(packet_size))
    chunk_size = max(1, size - _GA2_TYPE_B_HEADER_SIZE)
    total_len = len(payload)
    if total_len == 0:
        packet = bytearray(size)
        packet[0] = _GA2_TYPE_B
        packet[1] = command & 0xFF
        return [bytes(packet)]

    packets: list[bytes] = []
    part = 0
    for chunk in chunk_bytes(payload, chunk_size):
        packet = bytearray(size)
        packet[0] = _GA2_TYPE_B
        packet[1] = command & 0xFF
        packet[2:6] = total_len.to_bytes(4, "big", signed=False)
        packet[6:9] = part.to_bytes(3, "big", signed=False)
        packet[9:11] = len(chunk).to_bytes(2, "big", signed=False)
        packet[_GA2_TYPE_B_HEADER_SIZE : _GA2_TYPE_B_HEADER_SIZE + len(chunk)] = chunk
        packets.append(bytes(packet))
        part += 1
    return packets


def _encode_h264_from_jpeg(jpeg_payload: bytes) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH; cannot encode H.264 trial stream.")

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "mjpeg",
        "-i",
        "pipe:0",
        "-frames:v",
        "1",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "stillimage",
        "-pix_fmt",
        "yuv420p",
        "-f",
        "h264",
        "pipe:1",
    ]
    proc = subprocess.run(  # noqa: S603
        cmd,
        input=jpeg_payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"ffmpeg H.264 encode failed (code {proc.returncode}): {err or 'no output'}")
    return proc.stdout


def _encode_video_to_h264_stream(
    video_path: str,
    width: int,
    height: int,
    fps: float,
    max_seconds: float,
) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH; cannot encode video stream.")
    if fps <= 0:
        raise RuntimeError("fps must be > 0.")
    if max_seconds <= 0:
        raise RuntimeError("max_seconds must be > 0.")

    vf = (
        f"fps={fps},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
    )
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        video_path,
        "-t",
        f"{max_seconds:.3f}",
        "-an",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "1",
        "-keyint_min",
        "1",
        "-sc_threshold",
        "0",
        "-x264-params",
        "aud=1:repeat-headers=1",
        "-f",
        "h264",
        "pipe:1",
    ]
    proc = subprocess.run(  # noqa: S603
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"ffmpeg video encode failed (code {proc.returncode}): {err or 'no output'}")
    return proc.stdout


def _split_h264_access_units(stream: bytes, max_frames: int) -> list[bytes]:
    if not stream:
        return []

    markers: set[int] = set()
    for pat in (b"\x00\x00\x00\x01\x09", b"\x00\x00\x01\x09"):
        start = 0
        while True:
            idx = stream.find(pat, start)
            if idx < 0:
                break
            markers.add(idx)
            start = idx + 1

    starts = sorted(markers)
    if not starts:
        # No AUD marker found; treat as one frame.
        return [stream]

    frames: list[bytes] = []
    prefix = stream[: starts[0]]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(stream)
        frame = stream[start:end]
        if i == 0 and prefix:
            frame = prefix + frame
        if frame:
            frames.append(frame)
        if len(frames) >= max_frames:
            break
    return frames


@dataclass(slots=True)
class LianLiUsbBulkDevice:
    usb: UsbBulkDevice
    model: str
    capabilities: set[str] = field(default_factory=set)
    protocol: HidProtocolProfile | None = None

    @property
    def key(self) -> str:
        return f"{self.usb.vendor_id:04x}:{self.usb.product_id:04x}"

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.usb.id,
            "dev_node": self.usb.dev_node,
            "model": self.model,
            "vendor_id": f"0x{self.usb.vendor_id:04x}",
            "product_id": f"0x{self.usb.product_id:04x}",
            "manufacturer": self.usb.manufacturer,
            "product": self.usb.product,
            "serial": self.usb.serial,
            "capabilities": sorted(self.capabilities),
            "accessible": self.usb.accessible,
            "protocol_loaded": self.protocol is not None,
            "transport": "usb_bulk",
            "notes": self.protocol.notes if self.protocol else "",
            "endpoints": [ep.as_dict() for ep in self.usb.endpoints],
        }

    def upload_lcd_rgb565(self, frame: bytes, unsafe_hid_writes: bool = False) -> ActionResult:
        if self.protocol is None:
            return ActionResult(
                False,
                "No protocol profile loaded for this USB device. "
                "Create ~/.config/lianli-driver/protocols.json.",
            )
        if self.protocol.transport != "usb_bulk":
            return ActionResult(False, f"Protocol transport mismatch: {self.protocol.transport}")
        if self.protocol.lcd is None:
            notes = f" {self.protocol.notes}" if self.protocol.notes else ""
            return ActionResult(
                False,
                "LCD image upload protocol is not defined for this device."
                f"{notes}",
            )
        if not unsafe_hid_writes:
            return ActionResult(
                False,
                "Refusing to write unverified device protocol. Re-run with --unsafe-hid-writes.",
            )
        if not self.usb.accessible:
            return ActionResult(
                False,
                f"USB device is not accessible ({self.usb.dev_node}): {self.usb.error or 'permission denied'}",
            )

        lcd = self.protocol.lcd
        mode = lcd.mode.lower()
        packet_size = max(8, int(self.protocol.packet_size))
        endpoint = int(self.protocol.out_endpoint)
        in_endpoint = int(self.protocol.in_endpoint)

        if mode == "hydroshift_h264_guess":
            return self._upload_hydroshift_guess(frame, endpoint=endpoint, in_endpoint=in_endpoint, packet_size=packet_size)
        if mode == "wireless_jpg_des":
            return self._upload_wireless_jpg(frame, endpoint=endpoint, in_endpoint=in_endpoint)

        packets = 0

        def _bulk_send(payload: bytes) -> None:
            nonlocal packets
            for chunk in chunk_bytes(payload, packet_size):
                out = bytes(chunk)
                if len(out) < packet_size:
                    out = out + bytes(packet_size - len(out))
                write_usb_bulk_packet(self.usb, endpoint=endpoint, payload=out)
                packets += 1

        try:
            if mode == "raw":
                _bulk_send(frame)
            else:
                if lcd.begin:
                    _bulk_send(lcd.begin)
                seq = 0
                for chunk in chunk_bytes(frame, lcd.chunk_data_size):
                    payload = bytearray()
                    payload.extend(lcd.chunk_prefix)
                    if lcd.include_sequence_le16:
                        payload.extend(seq.to_bytes(2, byteorder="little", signed=False))
                    payload.extend(chunk)
                    _bulk_send(bytes(payload))
                    seq += 1
                if lcd.end:
                    _bulk_send(lcd.end)
        except Exception as exc:  # noqa: BLE001
            return ActionResult(
                False,
                f"USB bulk write failed on {self.usb.id}: {exc}",
                {"packets": packets, "endpoint": f"0x{endpoint:02x}"},
            )

        return ActionResult(
            True,
            f"Sent LCD frame to {self.usb.id}.",
            {"packets": packets, "endpoint": f"0x{endpoint:02x}"},
        )

    def stream_lcd_video(
        self,
        video_path: str,
        width: int,
        height: int,
        fps: float,
        max_seconds: float,
        unsafe_hid_writes: bool = False,
    ) -> ActionResult:
        if self.protocol is None:
            return ActionResult(
                False,
                "No protocol profile loaded for this USB device. "
                "Create ~/.config/lianli-driver/protocols.json.",
            )
        if self.protocol.transport != "usb_bulk":
            return ActionResult(False, f"Protocol transport mismatch: {self.protocol.transport}")
        if self.protocol.lcd is None:
            notes = f" {self.protocol.notes}" if self.protocol.notes else ""
            return ActionResult(
                False,
                "LCD video protocol is not defined for this device."
                f"{notes}",
            )
        if not unsafe_hid_writes:
            return ActionResult(
                False,
                "Refusing to write unverified device protocol. Re-run with --unsafe-hid-writes.",
            )
        if not self.usb.accessible:
            return ActionResult(
                False,
                f"USB device is not accessible ({self.usb.dev_node}): {self.usb.error or 'permission denied'}",
            )

        mode = self.protocol.lcd.mode.lower()
        if mode != "hydroshift_h264_guess":
            return ActionResult(
                False,
                f"Video streaming is currently supported only for hydroshift_h264_guess mode, got {mode}.",
            )

        try:
            h264_stream = _encode_video_to_h264_stream(
                video_path=video_path,
                width=max(16, int(width)),
                height=max(16, int(height)),
                fps=max(1.0, float(fps)),
                max_seconds=max(1.0, float(max_seconds)),
            )
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"Failed to encode video stream: {exc}")

        target_fps = max(1.0, float(fps))
        seconds = max(1.0, float(max_seconds))
        max_frames = max(1, int(target_fps * seconds))
        frames = _split_h264_access_units(h264_stream, max_frames=max_frames)
        if not frames:
            return ActionResult(False, "Video encode produced no H.264 frames.")

        endpoint = int(self.protocol.out_endpoint)
        in_endpoint = int(self.protocol.in_endpoint)
        packet_size = max(64, int(self.protocol.packet_size))
        packets_sent = 0
        errors: list[str] = []
        command_cycle = _GA2_STREAM_BURST_COMMANDS

        # Prime controller with best-effort GA-II style setup commands.
        for cmd, label in ((0x86, "ga2_fw_query"), (0x81, "ga2_handshake")):
            try:
                packet = _build_ga2_type_a_packet(cmd, packet_size=packet_size)
                write_usb_bulk_packet(self.usb, endpoint=endpoint, payload=packet, timeout_ms=1200)
                packets_sent += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}: {exc}")

        # Prime the wireless-compat sequence once to help mode switch on some firmwares.
        try:
            warmup = self._upload_wireless_jpg(
                payload=frames[0][: min(_WIRELESS_MAX_PAYLOAD, len(frames[0]))],
                endpoint=endpoint,
                in_endpoint=in_endpoint,
            )
            packets_sent += int(warmup.data.get("packets", 0))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"wireless_warmup: {exc}")

        interval = 1.0 / target_fps
        next_deadline = time.monotonic()
        frames_sent = 0
        for idx, frame in enumerate(frames):
            command = command_cycle[idx % len(command_cycle)]
            try:
                packets = _build_ga2_type_b_packets(
                    frame,
                    command=command,
                    packet_size=packet_size,
                )
                for packet in packets:
                    write_usb_bulk_packet(
                        self.usb,
                        endpoint=endpoint,
                        payload=packet,
                        timeout_ms=2000,
                    )
                packets_sent += len(packets)
                frames_sent += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"frame_{idx}_cmd_{command:02x}: {exc}")
                # Continue streaming remaining frames.
            next_deadline += interval
            sleep_for = next_deadline - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)

        return ActionResult(
            True,
            f"Sent HydroShift video stream to {self.usb.id}.",
            {
                "mode": "hydroshift_h264_guess",
                "endpoint": f"0x{endpoint:02x}",
                "in_endpoint": f"0x{in_endpoint:02x}",
                "fps_target": target_fps,
                "seconds": seconds,
                "frames_encoded": len(frames),
                "frames_sent": frames_sent,
                "packets_sent": packets_sent,
                "h264_bytes": len(h264_stream),
                "commands_used": [f"0x{cmd:02x}" for cmd in command_cycle],
                "errors": errors,
            },
        )

    def _upload_hydroshift_guess(
        self,
        jpeg_payload: bytes,
        endpoint: int,
        in_endpoint: int,
        packet_size: int,
    ) -> ActionResult:
        # First run the known-good wireless path used by TL wireless LCD devices.
        wireless_result = self._upload_wireless_jpg(jpeg_payload, endpoint=endpoint, in_endpoint=in_endpoint)
        total_packets = int(wireless_result.data.get("packets", 0))
        stream_steps: list[dict[str, object]] = []

        stream_payload = jpeg_payload
        stream_kind = "jpeg_passthrough"
        try:
            stream_payload = _encode_h264_from_jpeg(jpeg_payload)
            stream_kind = "h264_annexb"
            stream_steps.append({"command": "encode_h264", "ok": True, "bytes": len(stream_payload)})
        except Exception as exc:
            stream_steps.append(
                {
                    "command": "encode_h264",
                    "ok": False,
                    "error": str(exc),
                    "fallback": "jpeg_passthrough",
                }
            )

        # GA-II family documented Type-A control packets (best effort).
        for cmd, label in ((0x86, "ga2_fw_query"), (0x81, "ga2_handshake")):
            try:
                packet = _build_ga2_type_a_packet(cmd, packet_size=packet_size)
                write_usb_bulk_packet(self.usb, endpoint=endpoint, payload=packet, timeout_ms=1200)
                total_packets += 1
                step: dict[str, object] = {"command": label, "ok": True}
                try:
                    reply = read_usb_bulk_packet(
                        self.usb,
                        endpoint=in_endpoint,
                        size=max(64, packet_size),
                        timeout_ms=250,
                    )
                    if reply:
                        step["reply_hex_preview"] = reply[:64].hex()
                except Exception:
                    # HydroShift often does not ACK on IN endpoint.
                    pass
                stream_steps.append(step)
            except Exception as exc:
                stream_steps.append({"command": label, "ok": False, "error": str(exc)})

        # GA-II Type-B frame packet fallback.
        for command in _GA2_TYPE_B_CMD_CANDIDATES:
            try:
                packets = _build_ga2_type_b_packets(stream_payload, command=command, packet_size=packet_size)
                for packet in packets:
                    write_usb_bulk_packet(
                        self.usb,
                        endpoint=endpoint,
                        payload=packet,
                        timeout_ms=2000,
                    )
                total_packets += len(packets)
                step = {
                    "command": f"ga2_type_b_0x{command:02x}",
                    "ok": True,
                    "frames_sent": 1,
                    "packets_sent": len(packets),
                    "stream_kind": stream_kind,
                    "stream_bytes": len(stream_payload),
                }
                try:
                    reply = read_usb_bulk_packet(
                        self.usb,
                        endpoint=in_endpoint,
                        size=max(64, packet_size),
                        timeout_ms=250,
                    )
                    if reply:
                        step["reply_hex_preview"] = reply[:64].hex()
                except Exception:
                    pass
                stream_steps.append(step)
            except Exception as exc:
                stream_steps.append(
                    {
                        "command": f"ga2_type_b_0x{command:02x}",
                        "ok": False,
                        "error": str(exc),
                        "stream_kind": stream_kind,
                    }
                )

        # Continuous host-stream fallback: repeat the same frame quickly to emulate
        # the app-driven video feed expected by some GA II / HydroShift firmwares.
        burst_variants = ((512, 2), (64, 2))
        for burst_packet_size, burst_frames in burst_variants:
            for command in _GA2_STREAM_BURST_COMMANDS:
                try:
                    burst_packets = _build_ga2_type_b_packets(
                        stream_payload,
                        command=command,
                        packet_size=burst_packet_size,
                    )
                    for frame_index in range(burst_frames):
                        for packet in burst_packets:
                            write_usb_bulk_packet(
                                self.usb,
                                endpoint=endpoint,
                                payload=packet,
                                timeout_ms=2000,
                            )
                        total_packets += len(burst_packets)
                        # Small pacing delay to look more like a stream and avoid
                        # overfilling device-side buffers.
                        time.sleep(0.01)
                        stream_steps.append(
                            {
                                "command": f"ga2_stream_burst_0x{command:02x}",
                                "ok": True,
                                "packet_size": burst_packet_size,
                                "frame_index": frame_index,
                                "packets_sent": len(burst_packets),
                                "stream_kind": stream_kind,
                            }
                        )
                except Exception as exc:
                    stream_steps.append(
                        {
                            "command": f"ga2_stream_burst_0x{command:02x}",
                            "ok": False,
                            "packet_size": burst_packet_size,
                            "error": str(exc),
                            "stream_kind": stream_kind,
                        }
                    )

        detail: dict[str, object] = {
            "packets": total_packets,
            "endpoint": f"0x{endpoint:02x}",
            "in_endpoint": f"0x{in_endpoint:02x}",
            "mode": "hydroshift_h264_guess",
            "jpg_bytes": len(jpeg_payload),
            "stream_kind": stream_kind,
            "stream_bytes": len(stream_payload),
            "wireless_result": wireless_result.to_dict(),
            "ga2_typeb_steps": stream_steps,
        }

        # Transport writes succeeded even if the LCD still ignores the frame.
        return ActionResult(
            True,
            f"Sent HydroShift trial frame to {self.usb.id}.",
            detail,
        )

    def _upload_wireless_jpg(self, payload: bytes, endpoint: int, in_endpoint: int) -> ActionResult:
        if not payload:
            return ActionResult(False, "JPEG payload is empty.")
        packets = 0
        preview_reply = b""
        post_upload_steps: list[dict[str, object]] = []

        def _send_wireless(
            command: int,
            *,
            payload_data: bytes | None = None,
            single_byte: int | None = None,
            write_timeout_ms: int = 5000,
            read_timeout_ms: int = 500,
            expect_reply: bool = True,
        ) -> bytes:
            nonlocal packets
            packet = _build_wireless_packet(
                command,
                payload=payload_data,
                single_byte=single_byte,
            )
            write_usb_bulk_packet(
                self.usb,
                endpoint=endpoint,
                payload=packet,
                timeout_ms=write_timeout_ms,
            )
            packets += 1
            if not expect_reply:
                return b""
            return read_usb_bulk_packet(
                self.usb,
                endpoint=in_endpoint,
                size=max(_WIRELESS_HEADER_SIZE, int(self.protocol.packet_size) if self.protocol else 512),
                timeout_ms=read_timeout_ms,
            )

        def _send_fire_and_forget(
            command: int,
            *,
            single_byte: int | None = None,
            payload_data: bytes | None = None,
            timeout_ms: int = 800,
            label: str | None = None,
        ) -> None:
            nonlocal packets
            packet = _build_wireless_packet(
                command,
                payload=payload_data,
                single_byte=single_byte,
            )
            write_usb_bulk_packet(
                self.usb,
                endpoint=endpoint,
                payload=packet,
                timeout_ms=timeout_ms,
            )
            packets += 1
            post_upload_steps.append(
                {
                    "command": label or f"0x{command:02x}",
                    "single_byte": single_byte,
                    "ok": True,
                    "ack_expected": False,
                }
            )

        try:
            try:
                preview_reply = _send_wireless(
                    _WIRELESS_CMD_GET_POS_INDEX,
                    read_timeout_ms=1200,
                    expect_reply=True,
                )
            except Exception:
                preview_reply = b""

            # Some firmwares require leaving playback state before accepting a new still.
            try:
                _send_wireless(
                    _WIRELESS_CMD_STOP_PLAY,
                    read_timeout_ms=250,
                    expect_reply=False,
                )
            except Exception:
                pass

            _send_wireless(
                _WIRELESS_CMD_PUSH_JPG,
                payload_data=payload,
                write_timeout_ms=10000,
                expect_reply=False,
            )

            start_play_ok = False
            for label, single_byte in (("start_play", None), ("start_play_index_1", 1)):
                if start_play_ok:
                    break
                try:
                    reply = _send_wireless(
                        _WIRELESS_CMD_START_PLAY,
                        single_byte=single_byte,
                        read_timeout_ms=600,
                        expect_reply=True,
                    )
                    preview_reply = reply or preview_reply
                    post_upload_steps.append(
                        {
                            "command": label,
                            "single_byte": single_byte,
                            "ok": True,
                            "reply_hex_preview": reply[:64].hex() if reply else "",
                        }
                    )
                    start_play_ok = True
                except Exception as exc:
                    post_upload_steps.append(
                        {
                            "command": label,
                            "single_byte": single_byte,
                            "ok": False,
                            "error": str(exc),
                        }
                    )

            # Best-effort wake/position query after upload for displays that sleep quickly.
            try:
                reply = _send_wireless(
                    _WIRELESS_CMD_GET_POS_INDEX,
                    read_timeout_ms=400,
                    expect_reply=True,
                )
                preview_reply = reply or preview_reply
                post_upload_steps.append(
                    {
                        "command": "get_pos_index_post",
                        "ok": True,
                        "reply_hex_preview": reply[:64].hex() if reply else "",
                    }
                )
            except Exception as exc:
                post_upload_steps.append(
                    {
                        "command": "get_pos_index_post",
                        "ok": False,
                        "error": str(exc),
                    }
                )

            # HydroShift controllers can accept commands but never ACK on IN endpoint.
            # For this family, force a no-ack mode switch sequence after the JPEG upload.
            if self.key == _HYDROSHIFT_KEY:
                _send_fire_and_forget(
                    _WIRELESS_CMD_BRIGHTNESS,
                    single_byte=100,
                    label="brightness_100",
                )
                _send_fire_and_forget(
                    _WIRELESS_CMD_ROTATE,
                    single_byte=0,
                    label="rotate_0",
                )
                _send_fire_and_forget(
                    _WIRELESS_CMD_SET_FRAME_RATE,
                    single_byte=30,
                    label="fps_30",
                )
                _send_fire_and_forget(
                    _WIRELESS_CMD_STOP_CLOCK,
                    label="stop_clock",
                )
                # Try several mode/index values as one-shot commands to maximize
                # compatibility with firmware variants.
                for index in (None, 0, 1, 2, 3, 4, 5):
                    _send_fire_and_forget(
                        _WIRELESS_CMD_START_PLAY,
                        single_byte=index,
                        label="hydroshift_start_play",
                    )
        except Exception as exc:  # noqa: BLE001
            return ActionResult(
                False,
                f"Wireless JPEG upload failed on {self.usb.id}: {exc}",
                {
                    "packets": packets,
                    "endpoint": f"0x{endpoint:02x}",
                    "mode": "wireless_jpg_des",
                    "jpg_bytes": len(payload),
                },
            )

        detail: dict[str, object] = {
            "packets": packets,
            "endpoint": f"0x{endpoint:02x}",
            "in_endpoint": f"0x{in_endpoint:02x}",
            "mode": "wireless_jpg_des",
            "jpg_bytes": len(payload),
        }
        if post_upload_steps:
            detail["post_upload_steps"] = post_upload_steps
        if preview_reply:
            detail["reply_hex_preview"] = preview_reply[:64].hex()
        return ActionResult(
            True,
            f"Sent wireless JPEG frame to {self.usb.id}.",
            detail,
        )

    def _probe_wireless_channel(self) -> ActionResult:
        assert self.protocol is not None
        out_ep = int(self.protocol.out_endpoint)
        in_ep = int(self.protocol.in_endpoint)
        reply = b""
        try:
            query = _build_wireless_packet(_WIRELESS_CMD_GET_POS_INDEX)
            write_usb_bulk_packet(self.usb, endpoint=out_ep, payload=query, timeout_ms=5000)
            reply = read_usb_bulk_packet(
                self.usb,
                endpoint=in_ep,
                size=max(_WIRELESS_HEADER_SIZE, int(self.protocol.packet_size)),
                timeout_ms=2000,
            )
        except Exception as first_exc:  # noqa: BLE001
            try:
                query = _build_wireless_packet(_WIRELESS_CMD_GET_VER)
                write_usb_bulk_packet(self.usb, endpoint=out_ep, payload=query, timeout_ms=5000)
                reply = read_usb_bulk_packet(
                    self.usb,
                    endpoint=in_ep,
                    size=max(_WIRELESS_HEADER_SIZE, int(self.protocol.packet_size)),
                    timeout_ms=2000,
                )
            except Exception as second_exc:  # noqa: BLE001
                return ActionResult(
                    False,
                    "Wireless LCD probe failed.",
                    {"get_pos_index_error": str(first_exc), "get_ver_error": str(second_exc)},
                )

        ascii_preview = reply.replace(b"\x00", b" ").decode("ascii", errors="ignore").strip()
        return ActionResult(
            True,
            "Wireless probe completed.",
            {
                "mode": "wireless_jpg_des",
                "out_endpoint": f"0x{out_ep:02x}",
                "in_endpoint": f"0x{in_ep:02x}",
                "reply_hex": reply.hex(),
                "reply_ascii": ascii_preview,
            },
        )

    def _probe_hydroshift_channel(self) -> ActionResult:
        assert self.protocol is not None
        out_ep = int(self.protocol.out_endpoint)
        in_ep = int(self.protocol.in_endpoint)
        packet_size = max(64, int(self.protocol.packet_size))
        attempts: list[dict[str, object]] = []
        got_reply = False

        for cmd, label in ((0x86, "ga2_fw_query"), (0x81, "ga2_handshake")):
            for size in (64, packet_size):
                try:
                    packet = _build_ga2_type_a_packet(cmd, packet_size=size)
                    write_usb_bulk_packet(self.usb, endpoint=out_ep, payload=packet, timeout_ms=1200)
                    attempt: dict[str, object] = {
                        "command": label,
                        "packet_size": size,
                        "ok": True,
                    }
                    try:
                        reply = read_usb_bulk_packet(
                            self.usb,
                            endpoint=in_ep,
                            size=max(64, packet_size),
                            timeout_ms=500,
                        )
                        if reply:
                            got_reply = True
                            attempt["reply_hex_preview"] = reply[:64].hex()
                            attempt["reply_ascii_preview"] = (
                                reply[:64].replace(b"\x00", b" ").decode("ascii", errors="ignore").strip()
                            )
                    except Exception as exc:
                        attempt["read_error"] = str(exc)
                    attempts.append(attempt)
                except Exception as exc:
                    attempts.append(
                        {
                            "command": label,
                            "packet_size": size,
                            "ok": False,
                            "error": str(exc),
                        }
                    )

        # Also test the wireless handshake path used by TL receivers.
        try:
            query = _build_wireless_packet(_WIRELESS_CMD_GET_POS_INDEX)
            write_usb_bulk_packet(self.usb, endpoint=out_ep, payload=query, timeout_ms=2000)
            step: dict[str, object] = {"command": "wireless_get_pos_index", "ok": True}
            try:
                reply = read_usb_bulk_packet(
                    self.usb,
                    endpoint=in_ep,
                    size=max(_WIRELESS_HEADER_SIZE, packet_size),
                    timeout_ms=700,
                )
                if reply:
                    got_reply = True
                    step["reply_hex_preview"] = reply[:64].hex()
            except Exception as exc:
                step["read_error"] = str(exc)
            attempts.append(step)
        except Exception as exc:
            attempts.append({"command": "wireless_get_pos_index", "ok": False, "error": str(exc)})

        return ActionResult(
            got_reply,
            "HydroShift probe completed." if got_reply else "HydroShift probe completed (no IN replies).",
            {
                "mode": "hydroshift_h264_guess",
                "out_endpoint": f"0x{out_ep:02x}",
                "in_endpoint": f"0x{in_ep:02x}",
                "attempts": attempts,
            },
        )

    def probe_ga2_style_channel(self) -> ActionResult:
        if not self.usb.accessible:
            return ActionResult(
                False,
                f"USB device is not accessible ({self.usb.dev_node}): {self.usb.error or 'permission denied'}",
            )
        if self.protocol is None or self.protocol.transport != "usb_bulk":
            return ActionResult(False, "No bulk protocol available for probe.")
        if self.protocol.lcd is not None and self.protocol.lcd.mode.lower() == "hydroshift_h264_guess":
            return self._probe_hydroshift_channel()
        if self.protocol.lcd is not None and self.protocol.lcd.mode.lower() == "wireless_jpg_des":
            return self._probe_wireless_channel()

        packet_size = max(64, int(self.protocol.packet_size))
        out_ep = int(self.protocol.out_endpoint)
        in_ep = int(self.protocol.in_endpoint)
        # liquidctl GA2-style command: [type=1, cmd=0x86, ...]
        probe = bytes([1, 0x86, 0, 0, 0, 0]) + bytes(packet_size - 6)

        try:
            write_usb_bulk_packet(self.usb, endpoint=out_ep, payload=probe)
            reply = read_usb_bulk_packet(self.usb, endpoint=in_ep, size=packet_size)
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"GA2-style probe failed: {exc}")

        ascii_preview = bytes(reply).replace(b"\x00", b" ").decode("ascii", errors="ignore").strip()
        return ActionResult(
            True,
            "Probe completed.",
            {
                "out_endpoint": f"0x{out_ep:02x}",
                "in_endpoint": f"0x{in_ep:02x}",
                "reply_hex": bytes(reply).hex(),
                "reply_ascii": ascii_preview,
            },
        )
