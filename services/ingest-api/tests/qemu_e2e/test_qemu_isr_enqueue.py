from __future__ import annotations

import os
import pty
import select
import signal
import subprocess
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.qemu_e2e

ROOT_DIR = Path(__file__).resolve().parents[4]
FIRMWARE_DIR = ROOT_DIR / "firmware" / "test-apps" / "qemu-isr-enqueue"

QEMU_TARGET = os.getenv("AETUS_QEMU_TARGET", "esp32c3")
IDF_PATH = os.getenv("IDF_PATH", "")


def _build_firmware() -> None:
    if not IDF_PATH:
        pytest.skip("IDF_PATH not set")
    firmware_build_dir = FIRMWARE_DIR / f"build-{QEMU_TARGET}"
    subprocess.run(
        [
            os.path.join(IDF_PATH, "tools", "idf.py"),
            "-C", str(FIRMWARE_DIR),
            "-B", str(firmware_build_dir),
            "set-target", QEMU_TARGET,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            os.path.join(IDF_PATH, "tools", "idf.py"),
            "-C", str(FIRMWARE_DIR),
            "-B", str(firmware_build_dir),
            "build",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _find_qemu_binary() -> str:
    qemu_name = f"qemu-system-riscv32"
    result = subprocess.run(["which", qemu_name], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    raise RuntimeError(f"{qemu_name} not found in PATH")


def _parse_uart_output(output: str) -> dict:
    result = {
        "pass": False,
        "received": 0,
        "initial_hwm": 0,
        "final_hwm": 0,
        "fail_reason": None,
    }
    for line in output.split("\n"):
        line = line.strip()
        if "AETUS_ISR_ENQUEUE_PASS" in line:
            result["pass"] = True
        elif "AETUS_ISR_ENQUEUE_FAIL" in line:
            result["fail_reason"] = line
        elif "AETUS_ISR_ENQUEUE_RESULT" in line:
            parts = line.split()
            for part in parts:
                if part.startswith("received="):
                    result["received"] = int(part.split("=")[1])
                elif part.startswith("initial_hwm="):
                    result["initial_hwm"] = int(part.split("=")[1])
                elif part.startswith("final_hwm="):
                    result["final_hwm"] = int(part.split("=")[1])
    return result


@pytest.mark.skipif(
    os.getenv("AETUS_RUN_QEMU_E2E") != "1",
    reason="QEMU E2E requires AETUS_RUN_QEMU_E2E=1 and ESP-IDF toolchain",
)
def test_qemu_isr_enqueue_passes_and_stack_is_safe() -> None:
    _build_firmware()

    qemu_bin = _find_qemu_binary()
    firmware_build_dir = FIRMWARE_DIR / f"build-{QEMU_TARGET}"
    flash_bin = firmware_build_dir / "aetus_qemu_isr_enqueue.bin"

    if not flash_bin.exists():
        pytest.fail(f"firmware binary not found: {flash_bin}")

    qemu_cmd = [
        qemu_bin,
        "-nographic",
        "-machine", QEMU_TARGET,
        "-bios", str(flash_bin),
    ]

    master_fd, slave_fd = pty.openpty()
    try:
        proc = subprocess.Popen(
            qemu_cmd,
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=slave_fd,
        )

        output_lines: list[str] = []
        deadline = time.time() + 120.0
        done = False

        while time.time() < deadline and not done:
            ready, _, _ = select.select([master_fd], [], [], 5.0)
            if ready:
                try:
                    data = os.read(master_fd, 4096)
                    if data:
                        text = data.decode("utf-8", errors="replace")
                        output_lines.append(text)
                        if "AETUS_ISR_ENQUEUE_DONE" in text:
                            done = True
                except OSError:
                    break
            if proc.poll() is not None:
                break

        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        full_output = "".join(output_lines)
    finally:
        os.close(master_fd)
        os.close(slave_fd)

    result = _parse_uart_output(full_output)

    assert result["pass"], f"QEMU ISR enqueue test failed: {result['fail_reason']}\nOutput:\n{full_output}"
    assert result["received"] >= 2, f"expected at least 2 items, got {result['received']}"
    assert result["initial_hwm"] > 0, "initial stack high water mark not captured"
    assert result["final_hwm"] > 256, (
        f"stack dangerously low after ISR: final_hwm={result['final_hwm']} "
        f"(initial_hwm={result['initial_hwm']})"
    )
    stack_used = result["initial_hwm"] - result["final_hwm"]
    assert stack_used < 4096, (
        f"ISR stack usage too high: used={stack_used} bytes (may indicate excessive stack frame)"
    )
