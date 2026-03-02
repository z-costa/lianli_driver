from __future__ import annotations

from .base import LianLiUsbDevice


class UniFanTlController(LianLiUsbDevice):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.capabilities.update({"lcd"})
