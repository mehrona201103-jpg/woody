"""Способы передачи Woody. В MVP реализован только QR; остальные — расширяемые интерфейсы."""
from __future__ import annotations

from abc import ABC, abstractmethod


class TransferProvider(ABC):
    name: str

    @abstractmethod
    def build_payload(self, token: str, base_url: str) -> dict[str, str]:
        """Данные для передачи. Содержат только ссылку с одноразовым токеном — без личных данных."""


class QRTransferProvider(TransferProvider):
    name = "qr"

    def build_payload(self, token: str, base_url: str) -> dict[str, str]:
        return {"provider": self.name, "join_url": f"{base_url.rstrip('/')}/join/{token}"}


class NFCTransferProvider(TransferProvider):
    name = "nfc"

    def build_payload(self, token: str, base_url: str) -> dict[str, str]:
        raise NotImplementedError("TODO: NFC-передача (Web NFC нестабилен, нужен нативный Companion)")


class NearbyTransferProvider(TransferProvider):
    name = "nearby"

    def build_payload(self, token: str, base_url: str) -> dict[str, str]:
        raise NotImplementedError("TODO: Nearby/Bluetooth-передача (нужен нативный Companion)")


PROVIDERS: dict[str, TransferProvider] = {"qr": QRTransferProvider()}
