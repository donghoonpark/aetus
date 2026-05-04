# AETUS ESP32 QEMU Signal Pool Firmware

This validation firmware runs the reusable `firmware/esp32-aetus` component on an ESP-IDF QEMU RISC-V target. It does not post to the network. Instead, it exercises the public `aetus_enqueue_signal_frame()` path with `queue_depth=1` and two static signal sample pool blocks.

The expected runtime contract is:

- first enqueue succeeds and leaves one pool block owned by the queue
- second and third enqueue allocate a block, fail because the queue is full, and release the block immediately
- stats report `allocated_blocks=1`, `peak_allocated_blocks=2`, `allocation_count=3`, `release_count=2`, and `queue_send_failure_release_count=2`

Manual run:

```bash
idf.py set-target esp32c3
idf.py build
idf.py qemu monitor
```

The manual `ESP32 QEMU E2E` workflow also runs this firmware and waits for `AETUS_SIGNAL_POOL_TEST_DONE`.
