from __future__ import annotations

import os
import pty
import select
import shlex
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


def _run_idf_command(*args: str) -> None:
    if not IDF_PATH:
        pytest.skip("IDF_PATH not set")
    export_script = shlex.quote(str(Path(IDF_PATH) / "export.sh"))
    command = " ".join(shlex.quote(arg) for arg in args)
    subprocess.run(
        ["/bin/bash", "-lc", f"set -euo pipefail; source {export_script} >/dev/null; idf.py {command}"],
        check=True,
        capture_output=True,
        text=True,
        env=_idf_environment(),
    )


def _build_firmware() -> None:
    if not IDF_PATH:
        pytest.skip("IDF_PATH not set")
    firmware_build_dir = FIRMWARE_DIR / f"build-{QEMU_TARGET}"
    _run_idf_command("-C", str(FIRMWARE_DIR), "-B", str(firmware_build_dir), "set-target", QEMU_TARGET)
    _run_idf_command("-C", str(FIRMWARE_DIR), "-B", str(firmware_build_dir), "build")


def _find_qemu_binary() -> str:
    qemu_name = f"qemu-system-riscv32"
    result = subprocess.run(["which", qemu_name], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    raise RuntimeError(f"{qemu_name} not found in PATH")


def _capture_qemu_output_until(
    *,
    firmware_build_dir: Path,
    end_marker: str,
    timeout: float = 120.0,
) -> str:
    if not IDF_PATH:
        pytest.skip("IDF_PATH not set")
    export_script = shlex.quote(str(Path(IDF_PATH) / "export.sh"))
    command = (
        f"set -euo pipefail; source {export_script} >/dev/null; "
        f"idf.py -C {shlex.quote(str(FIRMWARE_DIR))} "
        f"-B {shlex.quote(str(firmware_build_dir))} qemu"
    )
    master_fd, slave_fd = pty.openpty()
    try:
        proc = subprocess.Popen(
            ["/bin/bash", "-lc", command],
            env=_idf_environment(),
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
        )
        os.close(slave_fd)
        slave_fd = -1

        output_lines: list[str] = []
        deadline = time.time() + timeout

        while time.time() < deadline:
            ready, _, _ = select.select([master_fd], [], [], 0.5)
            if ready:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    if proc.poll() is not None:
                        break
                    continue
                if data:
                    text = data.decode("utf-8", errors="replace")
                    output_lines.append(text)
                    if end_marker in text:
                        break
            if proc.poll() is not None:
                break

        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)

        return "".join(output_lines)
    finally:
        os.close(master_fd)
        if slave_fd >= 0:
            os.close(slave_fd)


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

    _find_qemu_binary()
    firmware_build_dir = FIRMWARE_DIR / f"build-{QEMU_TARGET}"
    full_output = _capture_qemu_output_until(
        firmware_build_dir=firmware_build_dir,
        end_marker="AETUS_ISR_ENQUEUE_DONE",
        timeout=120.0,
    )

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
