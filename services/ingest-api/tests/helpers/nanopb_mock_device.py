from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient
import importlib
import subprocess


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
    subprocess.run(["cmake", "--build", str(MOCK_BUILD_DIR)], check=True)

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

    def _build(self, mode: str, *extra: str) -> bytes:
        if mode == "telemetry":
            return self.module.encode_telemetry(self.device_id, self.boot_id, self.sequence)
        return self.module.encode_status(self.device_id, self.boot_id, self.sequence, extra[0] if extra else "power_on")

    def build_telemetry(self) -> bytes:
        return self._build("telemetry")

    def build_status(self, reboot_reason: str) -> bytes:
        return self._build("status", reboot_reason)

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
