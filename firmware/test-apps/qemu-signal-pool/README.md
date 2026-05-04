# AETUS ESP32 QEMU Signal Pool Firmware

This validation firmware runs the AETUS signal sample pool module on an ESP-IDF QEMU RISC-V target. It intentionally avoids Wi-Fi, HTTP, and backend services so the memory ownership contract can be checked cheaply on the target architecture.

The expected runtime contract is:

- first queue insert succeeds and leaves one pool block owned by the queue
- second and third inserts allocate a block, fail because the queue is full, log the overflow path, and release the block immediately
- stats report `allocated_blocks=1`, `peak_allocated_blocks=2`, `allocation_count=3`, `release_count=2`, and `queue_send_failure_release_count=2`

Manual run:

```bash
idf.py set-target esp32c3
idf.py build
idf.py qemu
```

The manual `ESP32 QEMU E2E` workflow also runs this firmware in foreground QEMU mode and waits for `AETUS_SIGNAL_POOL_TEST_DONE`.
