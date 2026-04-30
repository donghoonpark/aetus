from __future__ import annotations

import hashlib
import hmac
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import importlib
import subprocess

from aetus_ingest.auth import HMAC_SIGNATURE_PREFIX, HMAC_SIGNATURE_SCHEME


ROOT_DIR = Path(__file__).resolve().parents[4]
MOCK_DEVICE_DIR = ROOT_DIR / "services" / "mock-device-nanopb"
MOCK_BUILD_DIR = MOCK_DEVICE_DIR / "build"
INGEST_PROTO = ROOT_DIR / "services" / "ingest-api" / "proto" / "ingest.proto"
PYTHON_MODULE_DIR = MOCK_BUILD_DIR / "python"
_LOADED_MODULE = None


def ensure_mock_device_module_built():
    global _LOADED_MODULE
    if _LOADED_MODULE is not None:
        return _LOADED_MODULE

    subprocess.run(
        [
            "cmake",
            "-S",
            str(MOCK_DEVICE_DIR),
            "-B",
            str(MOCK_BUILD_DIR),
            f"-DINGEST_PROTO={INGEST_PROTO}",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        check=True,
    )
    subprocess.run(
        ["cmake", "--build", str(MOCK_BUILD_DIR), "--target", "mock_device", "mock_device_py"],
        check=True,
    )

    sys.path.insert(0, str(PYTHON_MODULE_DIR))
    _LOADED_MODULE = importlib.import_module("mock_device_py")
    return _LOADED_MODULE



class NanopbMockDevice:
    def __init__(self, device_id: str, token: str, boot_id: str = "boot-00000001") -> None:
        self.device_id = device_id
        self.token = token
        self.boot_id = boot_id
        self.sequence = 0
        self.module = ensure_mock_device_module_built()

    def _build(self, mode: str, *extra: str, timestamp_ns: int = 0) -> bytes:
        if mode == "telemetry":
            return self.module.encode_telemetry(self.device_id, self.boot_id, self.sequence, timestamp_ns)
        return self.module.encode_status(
            self.device_id,
            self.boot_id,
            self.sequence,
            extra[0] if extra else "power_on",
            timestamp_ns,
        )

    def build_telemetry(self, *, timestamp_ns: int = 0) -> bytes:
        return self._build("telemetry", timestamp_ns=timestamp_ns)

    def build_status(self, reboot_reason: str, *, timestamp_ns: int = 0) -> bytes:
        return self._build("status", reboot_reason, timestamp_ns=timestamp_ns)

    def upload(self, client: TestClient, payload: bytes):
        response = client.post(
            "/v1/ingest",
            content=payload,
            headers={
                "Content-Type": "application/x-protobuf",
                "X-Device-Id": self.device_id,
                "Authorization": f"Bearer {self.token}",
            },
        )
        self.sequence += 1
        return response

    def hmac_signature(self, payload: bytes) -> str:
        body_sha256_hex = hashlib.sha256(payload).hexdigest()
        signing_input = (
            f"{HMAC_SIGNATURE_PREFIX}\nPOST\n/v1/ingest\n{self.device_id}\n{body_sha256_hex}"
        ).encode("utf-8")
        signature = hmac.new(self.token.encode("utf-8"), signing_input, hashlib.sha256).hexdigest()
        return f"{HMAC_SIGNATURE_SCHEME}={signature}"

    def upload_hmac(self, client: TestClient, payload: bytes, *, signature: str | None = None):
        response = client.post(
            "/v1/ingest",
            content=payload,
            headers={
                "Content-Type": "application/x-protobuf",
                "X-Device-Id": self.device_id,
                "X-Aetus-Signature": signature or self.hmac_signature(payload),
            },
        )
        self.sequence += 1
        return response
