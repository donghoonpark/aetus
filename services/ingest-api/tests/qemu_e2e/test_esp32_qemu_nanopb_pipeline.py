from __future__ import annotations

import os
import pty
import select
import shlex
import signal
import subprocess
import time
from pathlib import Path

import httpx
import psycopg
import pytest


pytestmark = pytest.mark.qemu_e2e

ROOT_DIR = Path(__file__).resolve().parents[4]
COMPOSE_FILE = ROOT_DIR / "compose" / "e2e-compose.yml"
FIRMWARE_DIR = ROOT_DIR / "firmware" / "test-apps" / "qemu-telemetry"
SIGNAL_POOL_FIRMWARE_DIR = ROOT_DIR / "firmware" / "test-apps" / "qemu-signal-pool"
INGEST_API_URL = "http://127.0.0.1:18000"
POSTGRES_DSN = "postgresql://aetus:aetus@127.0.0.1:15432/aetus"
DEVICE_ID = "esp32c5-test-001"
DEVICE_TOKEN = "devtok_test_001"
BOOT_ID = "boot-qemu-0001"
SEQUENCE = 7
TIMESTAMP_NS = 1_712_345_678_901_235_000


def _docker_compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _wait_for_http(url: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=5.0)
            if response.status_code == 200:
                return
        except Exception as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def _wait_for_qemu_row(timeout: float = 120.0) -> tuple:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with psycopg.connect(POSTGRES_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        device_id,
                        boot_id,
                        sequence,
                        event_type,
                        timestamp_ns,
                        payload_json
                    FROM raw_device_events
                    WHERE device_id = %s AND boot_id = %s AND sequence = %s
                    """,
                    (DEVICE_ID, BOOT_ID, SEQUENCE),
                )
                row = cur.fetchone()
                if row is not None:
                    return row
        time.sleep(2)
    raise RuntimeError("Timed out waiting for QEMU payload row in PostgreSQL")


def _idf_path() -> Path:
    idf_path = os.getenv("IDF_PATH")
    if not idf_path:
        raise RuntimeError("IDF_PATH is required for qemu_e2e")
    export_script = Path(idf_path) / "export.sh"
    if not export_script.exists():
        raise RuntimeError(f"ESP-IDF export script not found: {export_script}")
    return Path(idf_path)


def _idf_environment() -> dict[str, str]:
    env = os.environ.copy()
    if env.get("IDF_PYTHON_ENV_PATH"):
        return env

    tools_path = Path(env.get("IDF_TOOLS_PATH", Path.home() / ".espressif"))
    python_env_dir = tools_path / "python_env"
    candidates = [
        path
        for path in python_env_dir.glob("idf6.0_py*_env")
        if (path / "bin" / "python").exists()
    ]
    if candidates:
        newest = max(candidates, key=lambda path: path.stat().st_mtime)
        env["IDF_PYTHON_ENV_PATH"] = str(newest)

    qemu_bins = [
        path
        for path in (tools_path / "tools" / "qemu-riscv32").glob("*/qemu/bin")
        if (path / "qemu-system-riscv32").exists()
    ]
    if qemu_bins:
        newest_qemu = max(qemu_bins, key=lambda path: path.stat().st_mtime)
        env["PATH"] = f"{newest_qemu}:{env.get('PATH', '')}"
    return env


def _idf_command(command: str, *, timeout: float, cwd: Path = FIRMWARE_DIR) -> subprocess.CompletedProcess[str]:
    idf_path = _idf_path()
    export_script = shlex.quote(str(idf_path / "export.sh"))
    return subprocess.run(
        ["/bin/bash", "-lc", f"set -euo pipefail; source {export_script} >/dev/null; {command}"],
        cwd=cwd,
        env=_idf_environment(),
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _capture_qemu_output_until(
    *,
    cwd: Path,
    end_marker: str,
    timeout: float = 120.0,
) -> list[str]:
    idf_path = _idf_path()
    export_script = shlex.quote(str(idf_path / "export.sh"))
    command = f"source {export_script} >/dev/null; idf.py qemu monitor"
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        ["/bin/bash", "-lc", command],
        cwd=cwd,
        env=_idf_environment(),
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    os.close(slave_fd)

    lines: list[str] = []
    partial = ""
    deadline = time.time() + timeout

    try:
        while time.time() < deadline:
            ready, _, _ = select.select([master_fd], [], [], 0.5)
            if not ready:
                if proc.poll() is not None:
                    break
                continue

            try:
                chunk = os.read(master_fd, 4096).decode(errors="replace")
            except OSError:
                if proc.poll() is not None:
                    break
                continue
            if not chunk:
                continue

            partial += chunk
            while "\n" in partial:
                line, partial = partial.split("\n", 1)
                stripped = line.strip()
                lines.append(stripped)
                if stripped == end_marker:
                    return lines
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
        os.close(master_fd)

    output_tail = "\n".join(lines[-80:])
    raise RuntimeError(f"Timed out waiting for QEMU marker {end_marker}:\n{output_tail}")


def _capture_qemu_foreground_output_until(
    *,
    cwd: Path,
    end_marker: str,
    timeout: float = 120.0,
) -> list[str]:
    idf_path = _idf_path()
    export_script = shlex.quote(str(idf_path / "export.sh"))
    command = f"source {export_script} >/dev/null; idf.py qemu"
    proc = subprocess.Popen(
        ["/bin/bash", "-lc", command],
        cwd=cwd,
        env=_idf_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )

    lines: list[str] = []
    deadline = time.time() + timeout

    try:
        assert proc.stdout is not None
        while time.time() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            stripped = line.strip()
            lines.append(stripped)
            if stripped == end_marker:
                return lines
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)

    output_tail = "\n".join(lines[-80:])
    raise RuntimeError(f"Timed out waiting for foreground QEMU marker {end_marker}:\n{output_tail}")


def _capture_qemu_payload_hex(timeout: float = 120.0) -> str:
    lines = _capture_qemu_output_until(cwd=FIRMWARE_DIR, end_marker="AETUS_PROTO_HEX_END", timeout=timeout)
    hex_lines: list[str] = []
    in_payload = False
    for line in lines:
        if line == "AETUS_PROTO_HEX_BEGIN":
            in_payload = True
            hex_lines = []
            continue
        if line == "AETUS_PROTO_HEX_END" and in_payload:
            return "".join(hex_lines)
        if in_payload:
            hex_lines.append(line)
    raise RuntimeError("QEMU output ended without a framed protobuf payload")


@pytest.fixture(scope="module")
def qemu_e2e_stack() -> None:
    if os.getenv("AETUS_RUN_QEMU_E2E") != "1":
        pytest.skip("Set AETUS_RUN_QEMU_E2E=1 to run ESP-IDF QEMU e2e")

    _idf_command("idf.py --version", timeout=60)
    _docker_compose("up", "-d", "--build")
    try:
        _wait_for_http(f"{INGEST_API_URL}/v1/healthz")
        yield
    finally:
        _docker_compose("down", "-v", "--remove-orphans")


def test_esp32_qemu_nanopb_stream_persists_to_postgres(qemu_e2e_stack: None) -> None:
    del qemu_e2e_stack
    target = os.getenv("AETUS_QEMU_TARGET", "esp32c3")

    _idf_command(f"idf.py set-target {shlex.quote(target)}", timeout=180)
    _idf_command("idf.py build", timeout=600)
    payload = bytes.fromhex(_capture_qemu_payload_hex())

    response = httpx.post(
        f"{INGEST_API_URL}/v1/ingest",
        content=payload,
        headers={
            "Content-Type": "application/x-protobuf",
            "X-Device-Id": DEVICE_ID,
            "Authorization": f"Bearer {DEVICE_TOKEN}",
        },
        timeout=10.0,
    )

    assert response.status_code == 202, response.text
    row = _wait_for_qemu_row()
    assert row[0:5] == (DEVICE_ID, BOOT_ID, SEQUENCE, "telemetry", TIMESTAMP_NS)
    assert '"key":"temperature"' in row[5]
    assert '"value":22.25' in row[5]


def test_esp32_qemu_signal_sample_pool_runtime_contract() -> None:
    if os.getenv("AETUS_RUN_QEMU_E2E") != "1":
        pytest.skip("Set AETUS_RUN_QEMU_E2E=1 to run ESP-IDF QEMU e2e")

    target = os.getenv("AETUS_QEMU_TARGET", "esp32c3")
    _idf_command("idf.py --version", timeout=60, cwd=SIGNAL_POOL_FIRMWARE_DIR)
    _idf_command(f"idf.py set-target {shlex.quote(target)}", timeout=180, cwd=SIGNAL_POOL_FIRMWARE_DIR)
    _idf_command("idf.py build", timeout=600, cwd=SIGNAL_POOL_FIRMWARE_DIR)

    lines = _capture_qemu_foreground_output_until(
        cwd=SIGNAL_POOL_FIRMWARE_DIR,
        end_marker="AETUS_SIGNAL_POOL_TEST_DONE",
        timeout=120.0,
    )
    output = "\n".join(lines)

    assert "AETUS_SIGNAL_POOL_STATIC_PASS" in output
    assert "signal frame enqueue failed because queue is full" in output
    assert "allocated_blocks=1" in output
    assert "peak_allocated_blocks=2" in output
    assert "allocation_count=3" in output
    assert "release_count=2" in output
    assert "queue_send_failure_release_count=2" in output
    assert "allocation_failure_count=0" in output
