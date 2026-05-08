# Client Packaging And Release

이 문서는 AETUS 클라이언트 라이브러리를 공개 패키지 레지스트리에 올리기 위한 배포 준비 절차를 정의한다.

## 대상 패키지

| Runtime | Package | Registry | Source |
| --- | --- | --- | --- |
| Python | `aetus-ingest-client` | PyPI | `clients/python-ingest` |
| Rust | `aetus-ingest-client` | crates.io | `clients/rust-ingest` |

두 클라이언트는 동일한 ingest protobuf 계약을 사용하며, 기본 bearer token 인증과 선택 HMAC 인증을 모두 지원한다.

## 배포 전 검증

Python:

```bash
cd clients/python-ingest
uv run pytest tests/unit -q
uv build
```

Rust:

```bash
cd clients/rust-ingest
cargo test --test unit_client
cargo publish --dry-run
```

E2E 검증은 Docker Compose 기반으로 PostgreSQL 적재까지 확인한다. 릴리스 후보를 만들기 전에는 가능한 한 로컬 또는 CI에서 Python/Rust e2e를 함께 실행한다.

## PyPI 배포

```bash
cd clients/python-ingest
uv build
uv publish
```

필요한 준비:

- `PYPI_TOKEN` 또는 `uv publish --token ...`
- `pyproject.toml`의 `version` 갱신
- `README.md`, `LICENSE`, `py.typed` 포함 여부 확인
- GitHub release note 또는 tag와 버전 일치

## crates.io 배포

```bash
cd clients/rust-ingest
cargo publish --dry-run
cargo publish
```

필요한 준비:

- `CARGO_REGISTRY_TOKEN`
- `Cargo.toml`의 `version` 갱신
- `README.md`, `LICENSE`, `proto/ingest.proto`, `build.rs`, `src/**/*.rs` 포함 여부 확인
- `cargo package --list`로 패키징 파일 목록 확인

## 인증 모드

기본 모드는 bearer token이다.

공개망에 가까운 환경 또는 위변조 방지 요구가 있는 환경에서는 HMAC 인증을 강력 권장한다. 서버에서 `AETUS_HMAC_AUTH_REQUIRED=true`를 설정하면 HMAC 없는 업로드는 거부된다.

Python:

```python
AetusIngestClient(
    base_url="http://127.0.0.1:18000",
    device_id="python-device-001",
    token="devtok_...",
    boot_id="boot-python-0001",
    auth_mode="hmac",
)
```

Rust:

```rust
AetusIngestClient::with_sequence_and_auth_mode(
    "http://127.0.0.1:18000",
    "rust-device-001",
    "devtok_...",
    "boot-rust-0001",
    42,
    0,
    AuthMode::HmacSha256,
)?;
```

## 버전 정책

초기 공개 전에는 `0.x` 버전을 사용한다.

- protobuf wire contract가 바뀌면 minor version을 올린다.
- backwards-compatible helper 추가는 patch version으로 처리한다.
- 서버 ingest 계약과 클라이언트 버전 호환성은 release note에 명시한다.

## 레지스트리 이름 확인

최초 공개 직전에는 레지스트리에서 패키지 이름이 비어 있는지 다시 확인한다. 이름 선점은 시간에 따라 바뀔 수 있으므로 release 직전에 재검증해야 한다.
