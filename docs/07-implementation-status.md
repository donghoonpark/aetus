# Implementation Status

## 목적

이 문서는 현재 저장소에 이미 구현된 내용을 빠르게 파악하기 위한 구현 상태 노트다.

- 어떤 서비스가 어디까지 구현되었는지
- 어떤 테스트가 실제로 돌아가는지
- 어떤 가정과 제약이 코드에 이미 반영되어 있는지
- 다음 에이전트가 어디서부터 이어받으면 되는지

설계 원칙은 `01~06` 문서를 따르고, 이 문서는 실제 코드 기준 현황을 기록한다.

## 현재 저장소 구조

```text
docs/
clients/
  python-ingest/
  rust-ingest/
compose/
firmware/
services/
  ingest-api/
  query-api/
  kafka/
  kafka-connect/
  postgres/
  mock-device-nanopb/
```

### 핵심 경계

- `services/ingest-api`
  - FastAPI 기반 ingest/provisioning/control plane
- `services/query-api`
  - FastAPI 기반 stream 조회, server-side downsampling, on-demand feature materialization
- `services/kafka`
  - self-managed Kafka 브로커 이미지
- `services/kafka-connect`
  - JDBC Sink 기반 PostgreSQL 적재
- `services/postgres`
  - raw event, metric staging, normalized metric point 저장용 TimescaleDB/PostgreSQL schema
- `services/mock-device-nanopb`
  - CMake + FetchContent + nanopb + pybind11 기반 mock device
- `firmware/test-apps/qemu-telemetry`
  - ESP-IDF 6.0 + nanopb 기반 QEMU E2E 전용 firmware stream generator
- `firmware/esp32-aetus`
  - ESP-IDF portable upload stack component
- `firmware/test-apps/esp32c5-upload-smoke`
  - `firmware/esp32-aetus`를 소비하는 ESP32-C5 HIL app
- `frontend/stream-viewer`
  - query-api용 portable Vue stream viewer component
- `clients/python-ingest`
  - Python producer SDK for protobuf ingest
- `clients/rust-ingest`
  - Rust producer SDK for protobuf ingest
- `compose/e2e-compose.yml`
  - 전체 파이프라인 및 query-api E2E 실행용 compose

## 구현 완료 범위

## 0. Producer Clients

구현 위치:

- [[../clients/python-ingest/src/aetus_ingest_client/client.py]]
- [[../clients/python-ingest/tests/unit/test_client.py]]
- [[../clients/python-ingest/tests/e2e/test_pipeline.py]]

현재 Python ingest client는 다음을 지원한다.

- bearer token 기반 `POST /v1/ingest`
- metric set 생성 및 업로드
- dense signal frame 생성 및 업로드
- status / alert event 생성 및 업로드
- row-major sample matrix에서 interleaved 또는 planar binary sample buffer packing
- 이미 packed 된 `bytes` signal payload 업로드
- 성공 응답에서만 local `sequence` 증가

Python client E2E는 compose stack을 띄운 뒤 provisioning으로 token을 발급하고, metric/status/signal frame을 업로드한 뒤 `raw_device_events`, `device_metric_points`, `device_signal_frames` 적재를 확인한다.

구현 위치:

- [[../clients/rust-ingest/src/lib.rs]]
- [[../clients/rust-ingest/tests/unit_client.rs]]
- [[../clients/rust-ingest/tests/e2e_pipeline.rs]]

현재 Rust ingest client는 다음을 지원한다.

- `prost` 기반 protobuf 생성
- vendored `protoc` 기반 build script
- bearer token 기반 `POST /v1/ingest`
- metric set 생성 및 업로드
- dense signal frame 생성 및 업로드
- status / alert event 생성 및 업로드
- `float32_le`, `int16_le`, `uint16_le`, `int32_le` sample packing
- interleaved / planar layout packing
- 성공 응답에서만 local `sequence` 증가

Rust client E2E도 compose stack을 통해 provisioning, ingest, Kafka/Kafka Connect, PostgreSQL raw/normalized 적재까지 확인한다.

## 1. Ingest API

구현 파일:

- [[../services/ingest-api/src/aetus_ingest/app.py]]
- [[../services/ingest-api/src/aetus_ingest/auth.py]]
- [[../services/ingest-api/src/aetus_ingest/normalize.py]]
- [[../services/ingest-api/src/aetus_ingest/publisher.py]]
- [[../services/ingest-api/src/aetus_ingest/rate_limit.py]]

현재 구현된 endpoint:

- `GET /v1/healthz`
- `GET /v1/readyz`
- `GET /v1/time`
- `POST /v1/ingest`
- `POST /v1/provision`
- `GET /v1/control/status`
- `GET /v1/control/devices`
- `POST /v1/control/devices/issue`
- `GET /admin/devices`
- `POST /admin/devices/issue`

### ingest 동작

`POST /v1/ingest`는 다음 순서로 처리된다.

1. `Content-Type=application/x-protobuf` 확인
2. source IP CIDR 확인
3. in-memory rate limit 검사
4. request body 읽기 및 크기 제한 확인
5. bearer 또는 HMAC 인증 방식 판별
6. bearer mode는 기존 device token 비교
7. HMAC mode는 raw body SHA256 계산 후 body hash 기반 signature 검증
8. protobuf 파싱
9. body 내부 `device_id`, `boot_id`, `body` 기본 검증
10. 내부 event object로 normalize
11. memory publisher 또는 Kafka publisher로 publish (Kafka publish는 `asyncio.to_thread()`로 event loop 비동기 처리)
12. in-memory rate limiter는 bucket idle 1시간 초과 시 자동 eviction (50,000개 bucket 한도)

HMAC-SHA256 선택 인증 경로는 구현되어 있으며 `X-Aetus-Signature: hmac-sha256-v1=<hex>`가 있으면 HMAC mode로 처리한다. HMAC mode에서도 control DB 조회는 read-only로 수행하고, ingest 경로는 control DB write를 하지 않는다.

### RTC time sync 동작

`GET /v1/time`은 장치별 정적 bearer token으로 인증하고 서버 시간을 반환한다.

- `unix_time_ns`는 JSON 정밀도 손실을 피하기 위해 문자열로 반환한다.
- 응답에는 `Cache-Control: no-store`가 붙는다.
- source IP CIDR, device token 검증, in-memory rate limit는 ingest 계열과 동일하게 적용한다.
- ESP32 표준 컴포넌트는 이 값을 받아 `settimeofday()`로 RTC를 설정하고, 이후 `timestamp_ns` helper로 telemetry/status에 시간을 채운다.

### 중요한 구현 결정

- ingest 경로는 control DB에 write 하지 않는다.
- SQLite backend의 ingest 인증 조회는 `aiosqlite` 기반 read-only connection으로 수행한다.
- PostgreSQL backend도 같은 `ControlStore` 인터페이스를 사용하며, control schema의 token/allowlist만 조회한다.
- `timestamp_ns`는 장치 시각으로 별도 보존하고, `received_at`은 서버 수신 시각으로 별도 기록한다.
- `sequence` 순서가 꼬여 들어와도 ingest 레벨에서는 막지 않는다.
- HMAC 인증에서도 ingest 경로 DB write 원칙을 유지하고, replay guard는 별도 확장으로 둔다.

## 2. Provisioning / Control Plane

구현 파일:

- [[../services/ingest-api/src/aetus_ingest/control_db.py]]
- [[../services/ingest-api/src/aetus_ingest/control_backup.py]]
- [[../services/ingest-api/src/aetus_ingest/schemas.py]]

현재 control DB는 `SQLite`와 `PostgreSQL` backend를 선택할 수 있다.

역할:

- hardware allowlist 저장
- device token 저장
- provisioning 시 신규 token 발급
- admin page용 device list 조회
- control panel용 JSON API 제공

### backend 선택

설정:

- `AETUS_CONTROL_DB_BACKEND=sqlite|postgres`
- `AETUS_CONTROL_DB_PATH=data/control.db`
- `AETUS_CONTROL_DATABASE_URL=postgresql://...`
- `AETUS_CONTROL_DB_SCHEMA=control`

`AETUS_CONTROL_DATABASE_URL`이 없으면 `AETUS_POSTGRES_DSN`을 control DB 연결에도 사용한다. 단, telemetry table과 control table은 같은 database 안에서도 schema를 분리한다.

### SQLite 사용 방식

- 단일 Pod, 초기 PoC, 랩 환경의 기본 backend다.
- read path: `aiosqlite`
- write path: provisioning / admin issue only
- 설정:
  - `journal_mode=WAL`
  - `synchronous=NORMAL`
  - `busy_timeout=3000`
- FastAPI lifespan task가 SQLite online backup API로 주기 백업을 만든다.
- 기본 백업 설정:
  - `AETUS_CONTROL_DB_BACKUP_ENABLED=true`
  - `AETUS_CONTROL_DB_BACKUP_DIR=data/control-backups`
  - `AETUS_CONTROL_DB_BACKUP_INTERVAL_SECONDS=3600`
  - `AETUS_CONTROL_DB_BACKUP_RETENTION_COUNT=48`
  - `AETUS_CONTROL_DB_BACKUP_ON_STARTUP=true`

compose 환경에서는 `/data/control-backups`가 volume에 남는다.

### PostgreSQL 사용 방식

- multi-pod 또는 운영 환경의 권장 backend다.
- 기본 schema는 `control`이다.
- 생성 table:
  - `control.devices`
  - `control.hardware_allowlist`
- ingest upload 경로는 PostgreSQL backend를 써도 token/allowlist read만 수행한다.
- provisioning과 admin issue API만 control DB write를 수행한다.

### 현재 전환 기준

운영 가정은 다음과 같다.

- 초기 운용: `SQLite + 주기 백업`
- 다중 Pod 또는 호출량 증가: `PostgreSQL control backend`
- telemetry 저장소는 기존 PostgreSQL/TimescaleDB와 유지하되, control plane schema는 분리한다.

## 3. Admin Console

템플릿 파일:

- [[../services/ingest-api/src/aetus_ingest/templates/admin_devices.html]]

현재 지원 기능:

- 신규 장치 token 발급
- 페이지네이션
- 검색 필터
  - `device_id`
  - `hardware_id`
  - `model`
  - `site_code`
- 최근 발급 장치 row highlight
- token copy button
- Bootstrap + Font Awesome 기반 스타일
- `AETUS` 브랜딩 반영

주의:

- 현재 admin page는 인증이 없다.
- 분리망 내부 운영 도구라는 전제를 둔 상태다.
- 외부 노출 환경으로 가면 별도 보호장치가 필요하다.

## 3-1. Portable Vue Control Panel

구현 위치:

- [[../frontend/ingest-control-panel/src/IngestControlPanel.vue]]
- [[../frontend/ingest-control-panel/src/index.ts]]
- [[../frontend/ingest-control-panel/src/demo/App.vue]]
- [[../frontend/ingest-control-panel/package.json]]

현재 방향:

- `Vue 3 + Naive UI`
- 단일 컴포넌트 export
- `serverUrl` prop 기반
- 다른 admin shell에 이식 가능한 구조

현재 지원 기능:

- 상태 카드
  - API
  - Control DB
  - Kafka
  - Kafka Connect
  - PostgreSQL
- 장치 검색
- 장치 목록 pagination
- token 발급
- token copy

로컬 빌드:

```bash
cd frontend/ingest-control-panel
npm install
npm run build
```

## 4. Kafka / Kafka Connect / TimescaleDB/PostgreSQL

구현 파일:

- [[../compose/e2e-compose.yml]]
- [[../services/kafka-connect/sink-config.json]]
- [[../services/kafka-connect/connectors/raw-device-events-sink.json]]
- [[../services/kafka-connect/connectors/metric-ingest-staging-sink.json]]
- [[../services/kafka-connect/connectors/signal-frame-ingest-staging-sink.json]]
- [[../services/postgres/initdb/00-base.sql]]
- [[../services/postgres/initdb/10-timescale.sql]]

현재 적재 흐름:

`FastAPI -> Kafka -> Kafka Connect JDBC Sink -> TimescaleDB/PostgreSQL`

현재 Kafka Connect sink는 세 개다.

- `raw-device-events-sink`: `device.raw.v1`을 `raw_device_events`에 upsert
- `metric-ingest-staging-sink`: `device.metric.v1`을 `metric_ingest_staging`에 upsert
- `signal-frame-ingest-staging-sink`: `device.signal_frame.v1`을 `signal_frame_ingest_staging`에 upsert

### Kafka publish 형식

`publisher.py`는 Connect JDBC Sink가 이해할 수 있도록 schema-enabled JSON envelope를 Kafka에 넣는다.

즉 Kafka value는 대략 아래 구조다.

```json
{
  "schema": { "...": "..." },
  "payload": {
    "device_id": "esp32c5-001",
    "boot_id": "boot-0001",
    "sequence": 0,
    "timestamp_ns": 1712345678901234567,
    "payload_json": "{\"metrics\":[...]}"
  }
}
```

### PostgreSQL raw table

테이블:

- `raw_device_events`

현재 저장 컬럼:

- `device_id`
- `boot_id`
- `sequence`
- `event_type`
- `schema_version`
- `firmware_version`
- `uptime_ms`
- `timestamp_ns`
- `request_id`
- `received_at`
- `source_ip`
- `payload_json`

중복 방지 키:

- PK: `(device_id, boot_id, sequence)`

### TimescaleDB metric tables

장기 분석용 metric과 signal frame은 raw JSON과 분리해 TimescaleDB hypertable에 저장한다.

테이블:

- `metric_ingest_staging`
- `signal_frame_ingest_staging`
- `devices`
- `device_boot_sessions`
- `metric_definitions`
- `signal_stream_definitions`
- `device_metric_points` hypertable
- `device_signal_frames` hypertable

흐름:

1. FastAPI가 telemetry payload의 `metrics`를 metric별 Kafka record로 펼쳐 `device.metric.v1`에 publish
2. Kafka Connect JDBC Sink가 `metric_ingest_staging`에 upsert
3. PostgreSQL trigger `ingest_metric_staging_row()`가 dimension table을 upsert
4. 같은 trigger가 `device_metric_points`에 정수 key 기반 point row를 upsert
5. FastAPI가 telemetry payload의 `signal_frame`을 `device.signal_frame.v1`에 publish
6. Kafka Connect JDBC Sink가 `signal_frame_ingest_staging`에 upsert
7. PostgreSQL trigger `ingest_signal_frame_staging_row()`가 `device_signal_frames`에 sample block을 upsert

시간 처리:

- `timestamp_ns`가 있으면 `device_metric_points.event_time`은 장치 timestamp 기준
- `timestamp_ns`가 있으면 `device_signal_frames.event_time`도 frame 첫 sample의 장치 timestamp 기준
- `timestamp_ns`가 없으면 `received_at` 기준
- 원본 ns 값은 `event_time_ns`에 그대로 보관

보관 정책 목표:

- `raw_device_events`: 1일 수준의 짧은 디버깅 보관
- `metric_ingest_staging`: raw와 같은 짧은 보관
- `signal_frame_ingest_staging`: raw와 같은 짧은 보관
- `device_metric_points`: TimescaleDB retention policy로 1년 수준의 장기 보관
- `device_signal_frames`: TimescaleDB retention policy로 1년 수준의 장기 보관
- `signal_frame_features`: query 시점에 생성되는 on-demand materialized cache, 자체 retention으로 삭제
- `signal_rollup_points`: 고정 `x4` tier 기반 query-serving rollup table

TimescaleDB 설정:

- `00-base.sql`은 plain PostgreSQL fallback 가능한 table/index/trigger 정의
- `10-timescale.sql`은 TimescaleDB extension, hypertable, compression, retention 정책 정의
- `CREATE EXTENSION IF NOT EXISTS timescaledb`
- `device_metric_points(event_time)` hypertable
- `device_signal_frames(event_time)` hypertable
- `signal_rollup_points(bucket_start)` hypertable
- `7일` 경과 chunk compression policy
- `1년` 경과 metric/signal frame retention policy
- hypertable unique 제약 조건은 time partition column을 포함하기 위해 `UNIQUE (event_time, request_id, metric_index)` 사용
- signal frame hypertable unique 제약 조건은 `UNIQUE (event_time, request_id)` 사용

## 4-1. Query API

구현 파일:

- [[../services/query-api/src/aetus_query/app.py]]
- [[../services/query-api/src/aetus_query/repository.py]]
- [[../services/query-api/src/aetus_query/signal_decode.py]]
- [[../services/query-api/src/aetus_query/cache.py]]

현재 구현된 endpoint:

- `GET /v1/healthz`
- `GET /v1/readyz`
- `GET /v1/query/devices/{device_id}/streams`
- `GET /v1/query/devices/{device_id}/streams/{key}/series`
- `GET /v1/query/devices/{device_id}/streams/{key}/summary`
- `GET /v1/query/devices/{device_id}/streams/{key}/frames`

현재 구현된 동작:

- 공개 조회 모델은 `metric`과 `signal frame`를 숨기고 `stream`으로 통합 노출
- `kind=scalar` stream은 `device_metric_points`에서 series 조회
- `kind=sampled` stream은 `device_signal_frames` 또는 `signal_rollup_points`에서 series 조회
- rollup row가 없으면 raw frame을 query-api에서 decode해 sample-level series 또는 sample-bucket envelope 반환
- `summary` 요청은 query-api 런타임에서 raw `BYTEA samples`를 decode해 `signal_frame_features`를 on-demand upsert
- 만료된 `signal_frame_features`는 query-api가 materialization 시 삭제
- `frames`는 좁은 구간의 sampled stream에 대해서만 raw decoded sample JSON 반환
- `Redis` cache를 선택적으로 사용하며, 실패 시 DB 조회 경로로 fallback (`RedisError` 발생 시 `logger.warning`으로 기록)
- JSON 응답은 `GZipMiddleware` 기반 `Accept-Encoding` 압축을 지원
- `PostgresQueryRepository`는 `psycopg_pool.ConnectionPool` 기반 connection pooling 사용 (`min_size=2, max_size=10`)
- repository method는 debug level로 쿼리 지연시간과 row count 로깅

중요한 구현 결정:

- raw binary format 해석은 PostgreSQL 내부 함수가 아니라 query-api 애플리케이션 코드에서 수행
- PostgreSQL은 raw 저장, 범위 조회, feature/rollup upsert, retention을 담당
- query-api 인증은 아직 구현 범위 밖이며 open decision으로 남아 있다

고밀도 테스트 데이터:

- [[../services/query-api/tools/seed_dense_query_data.py]]
- 기본값은 `1시간 / 1,002,000 sample point / float32_le / interleaved` signal frame을 생성한다.
- `device_signal_frames`에 frame block 단위로 삽입하므로 대량 point를 row-per-sample로 만들지 않는다.

## 4-2. Portable Vue Stream Viewer

구현 위치:

- [[../frontend/stream-viewer/src/AetusStreamViewer.vue]]
- [[../frontend/stream-viewer/src/index.ts]]
- [[../frontend/stream-viewer/src/demo/App.vue]]
- [[../frontend/stream-viewer/tests/e2e/stream-viewer.spec.ts]]

현재 방향:

- `Vue 3 + Naive UI + ECharts`
- 단일 컴포넌트 export: `AetusStreamViewer`
- `queryServerUrl` prop 기반
- 다른 운영 콘솔에 이식 가능한 구조

현재 지원 기능:

- device ID 입력
- stream 목록 조회
- scalar/sampled stream 구분 표기
- `GET /series` 기반 chart 렌더링
- scalar stream line chart
- sampled stream channel별 min/max envelope chart
- sampled stream raw sample value chart
- `10m`, `1h`, `6h`, `1d` 범위 preset
- `max_points` 제어

로컬 빌드와 테스트:

```bash
cd frontend/stream-viewer
npm install
npm run build
npm run test:e2e
```

## 5. Mock Device

구현 위치:

- [[../services/mock-device-nanopb/CMakeLists.txt]]
- [[../services/mock-device-nanopb/src/mock_device_core.c]]
- [[../services/mock-device-nanopb/src/mock_device_pybind.cpp]]
- [[../services/ingest-api/tests/helpers/nanopb_mock_device.py]]

### 구현 특징

- `CMake`
- `FetchContent`
  - `nanopb`
  - `pybind11`
- `grpc_tools.protoc` + `protoc-gen-nanopb`
- Python 테스트에서 `pybind11` 모듈을 import해서 실제 nanopb 직렬화 bytes 사용

### 지원하는 mock payload

- telemetry
- status
- `timestamp_ns` 포함 telemetry/status

## 6. ESP32 QEMU Firmware E2E

구현 위치:

- [[../firmware/test-apps/qemu-telemetry]]
- [[../services/ingest-api/tests/qemu_e2e/test_esp32_qemu_nanopb_pipeline.py]]
- [[../.github/workflows/qemu-e2e.yml]]

목적:

- Python/host mock이 아니라 ESP-IDF로 실제 firmware binary를 빌드한다.
- QEMU에서 firmware를 실행한다.
- firmware가 UART로 출력한 nanopb protobuf byte stream을 테스트가 수집한다.
- 수집한 byte stream을 `POST /v1/ingest`로 업로드한다.
- Kafka/Kafka Connect를 거쳐 PostgreSQL에 적재되는지 검증한다.

운영 방식:

- 일반 CI에는 포함하지 않는다.
- `qemu_e2e` pytest marker로 분리한다.
- GitHub Actions에서는 `ESP32 QEMU E2E` workflow를 수동 실행한다.
- 생산 표준 target은 `esp32c5`이지만, ESP-IDF 6.0 QEMU는 현재 `esp32c5` target을 지원하지 않는다.
- QEMU E2E 기본 target은 `esp32c3`로 둔다.
- 이 테스트는 RISC-V ESP32 firmware binary에서 동일 nanopb encode path가 동작하는지 검증한다.
- C5 QEMU 지원이 열리면 `AETUS_QEMU_TARGET=esp32c5`로 바꿔 돌릴 수 있다.

실행 예:

```bash
cd services/ingest-api
IDF_PATH=/path/to/esp-idf \
AETUS_RUN_QEMU_E2E=1 \
AETUS_QEMU_TARGET=esp32c3 \
uv run pytest tests/qemu_e2e -q -s
```

### 6-1. QEMU ISR Enqueue Test

구현 위치:

- [[../firmware/test-apps/qemu-isr-enqueue]]
- [[../services/ingest-api/tests/qemu_e2e/test_qemu_isr_enqueue.py]]

목적:

- ESP-IDF `gptimer` ISR callback에서 `aetus_enqueue_telemetry_from_isr()`와 `aetus_enqueue_status_from_isr()`의 패턴을 검증한다.
- ISR context에서 `aetus_queue_item_t` struct stack frame이 overflow를 일으키지 않는지 확인한다.
- Queue에 정상적으로 item이 전달되는지, telemetry/status item이 모두 수신되는지 검증한다.
- `uxTaskGetStackHighWaterMark`로 ISR 실행 전후 stack 사용량을 확인한다.

검증 항목:

1. firmware build (`esp32c3` QEMU target)
2. QEMU에서 `gptimer` ISR을 통해 telemetry + status item enqueue
3. main task에서 queue drain 후 item 수신 확인 (telemetry + status 2개)
4. ISR 실행 후 stack high water mark가 256 bytes 이상 남았는지 확인
5. ISR stack 사용량이 4096 bytes 미만인지 확인

## 7. ESP32 AETUS Portable Upload Stack

구현 위치:

- [[../firmware/esp32-aetus]]
- [[../firmware/esp32-aetus/components/aetus/include/aetus.h]]
- [[../firmware/examples]]
- [[../firmware/test-apps/qemu-telemetry]]
- [[../firmware/test-apps/qemu-isr-enqueue]]
- [[../firmware/test-apps/esp32c5-upload-smoke]]
- [[../firmware/test-apps/esp32c5-isr-enqueue]]

목적:

- ESP32-C5 제품 firmware에서 공통으로 쓸 업로드 스택을 제공한다.
- 유저 비즈니스 로직은 thread-safe enqueue API만 호출한다.
- 별도 uploader task가 queue, upload timer, Wi-Fi, nanopb encode, HTTP POST를 담당한다.

현재 공개 API:

- `aetus_start` / `aetus_update_config` / `aetus_get_config`
- `aetus_start_provisioning`
- `aetus_sync_rtc` / `aetus_rtc_timestamp_ns`
- `aetus_telemetry_init` / `aetus_telemetry_deinit`
- `aetus_telemetry_set_timestamp_rtc` / `aetus_status_set_timestamp_rtc`
- `aetus_enqueue_telemetry` / `aetus_enqueue_status` / `aetus_enqueue_signal_frame`
- `aetus_enqueue_telemetry_from_isr` / `aetus_enqueue_status_from_isr` (`CONFIG_AETUS_ISR_SAFE_ENQUEUE`)
- `aetus_telemetry_add_int64` / `add_double` / `add_bool` / `add_string` / `add_string_n` / `add_bytes`
- `aetus_get_signal_sample_pool_stats`
- `aetus_flush`

현재 구현된 runtime 동작:

- boot ID 자동 생성
- sequence 0부터 시작
- 서버 2xx 수락 후 sequence 증가
- telemetry/status event protobuf encode
- double/int64/bool/string/bytes metric value encode
- `/v1/time` 기반 RTC sync
- RTC 기반 `timestamp_ns` helper
- upload 실패 시 queue front requeue
- `aetus_flush()` 완료 대기
- bearer/HMAC ingest auth mode 선택
- WPA2-Enterprise PEAP Wi-Fi 연결 경로
- NimBLE GATT provisioning으로 Wi-Fi/API 설정 갱신
- Wi-Fi connected LED 제어
- C++20 wrapper API
- heap 기반 signal sample memory pool (정적 pool backend 제거)
- telemetry/status ISR-safe enqueue API (`CONFIG_AETUS_ISR_SAFE_ENQUEUE`, global BSS buffer)
- `AETUS_MAX_METRICS` Kconfig 옵션 (기본 32, 범위 4~32, inline_metrics[4] + heap_metrics overflow)

ISR-safe enqueue 구현 메모:

- `aetus_enqueue_telemetry_from_isr()`와 `aetus_enqueue_status_from_isr()`만 제공한다.
- C++ wrapper는 `Telemetry::enqueue_from_isr()`와 `Status::enqueue_from_isr()`를 같은 `CONFIG_AETUS_ISR_SAFE_ENQUEUE` guard 아래 제공한다.
- 권장 사용 패턴은 ISR 밖에서 telemetry/status 객체를 준비하고 ISR 안에서는 enqueue만 수행하는 방식이다.
- `SignalFrame`은 sample pool allocation/copy가 필요하므로 ISR-safe API를 제공하지 않는다.
- ISR 경로는 task stack에 큰 `aetus_queue_item_t`를 만들지 않도록 BSS global buffer와 spinlock을 사용한다.
- 이 API는 queue full 시 `ESP_ERR_TIMEOUT`을 반환하며 ISR 내부에서 `ESP_LOG`를 호출하지 않는다.
- telemetry ISR 경로는 inline scalar metric만 허용하므로 `AETUS_TELEMETRY_INLINE_METRICS`(기본 4개)를 초과하면 `ESP_ERR_INVALID_ARG`를 반환한다.

예제 app:

- `firmware/examples/basic-telemetry`: 최소 telemetry/status enqueue 예제
- `firmware/examples/multitask-producers`: 여러 FreeRTOS producer task에서 enqueue하는 예제
- `firmware/examples/metric-types`: int64/double/bool/string/bytes metric type 예제
- `firmware/examples/cpp-friendly-interface`: repository-level C++ wrapper 예제
- `firmware/examples/cpp-basic`: C++20 wrapper/HIL 예제
- `firmware/examples/cpp-signal-frame`: dense `SignalFrame` 업로드 예제
- `firmware/examples/cpp-light-sleep`: tickless idle/light sleep 관찰용 예제
- 모든 예제는 ESP32-C5, ESP-IDF 6.0, 4MB flash, 3MB factory app partition 기준으로 빌드한다.

검증용 firmware:

- `firmware/test-apps/qemu-telemetry`: RISC-V QEMU protobuf stream 생성 및 DB 적재 검증
- `firmware/test-apps/qemu-isr-enqueue`: QEMU에서 ISR-safe enqueue와 stack high-water mark 검증
- `firmware/test-apps/qemu-telemetry-heap`: QEMU에서 dynamic metric heap storage, blob deep-copy, queue item release counter, heap recovery 검증
- `firmware/test-apps/esp32c5-upload-smoke`: 실제 ESP32-C5 업로드 HIL 검증
- `firmware/test-apps/esp32c5-isr-enqueue`: 실제 ESP32-C5 gptimer ISR enqueue + 4개 초과 metric overflow 거부 + 업로드 HIL 검증
- `firmware/test-apps/cpp-literal-limit-negative`: C++ wrapper overlong metric key literal static_assert 검증용 negative build

HIL firmware는 개인 Wi-Fi/API credential을 repository에 저장하지 않는다. `AETUS_WIFI_SSID`, `AETUS_WIFI_PASSWORD`, `AETUS_INGEST_URL`, `AETUS_DEVICE_ID`, `AETUS_DEVICE_TOKEN` 환경변수를 통해 build-time config header를 생성한다.

현재 미구현:

- FlashDB durable backlog
- 대형 payload용 pointer/blob queue API
- Wi-Fi ownership adapter
- HTTPS client/certificate policy
- server-side provisioning API client

## 테스트 현황

테스트 실행 위치:

- [[../services/ingest-api]]
- [[../services/query-api]]

실행 명령:

```bash
uv run pytest -q
```

현재 통과 기준:

- ingest unit: `39 passed`
- ingest PostgreSQL/Kafka e2e: `23 passed`
- query-api unit/e2e: `17 passed`
- stream-viewer frontend e2e: `2 passed`
- QEMU e2e: 기본 실행에서는 skip, `AETUS_RUN_QEMU_E2E=1`일 때 별도 실행

### unit coverage

현재 unit test는 다음을 커버한다.

- 정상 telemetry 업로드
- 정상 reboot status 업로드
- invalid token 거부
- HMAC 정상 업로드
- invalid HMAC signature 거부
- 수정된 body에 대한 HMAC 재사용 거부
- unknown HMAC scheme 거부
- provisioning 후 발급 token으로 ingest 가능
- provisioning bootstrap rate limit
- control JSON API 기반 token 발급/조회
- control status endpoint component state 확인
- `/v1/time` 인증 및 응답 형식
- `timestamp_ns` normalize 보존
- out-of-order `sequence` 허용
- ingest rate limit과 allowlist relaxed limit
- admin page 브랜딩 렌더
- admin page pagination
- admin page search + copy token control 렌더
- admin password 인증 및 session cookie 기반 login/logout
- `AETUS_ADMIN_PASSWORD` 미설정 시 기존 동작 유지 (하위 호환)

### e2e coverage

현재 e2e는 다음을 커버한다.

1. docker compose로 전체 스택 기동
2. `POST /v1/provision`으로 device token 발급
3. `GET /v1/control/devices`에서 발급 장치 조회 가능 확인
4. `GET /v1/control/status`에서 API/Kafka/Connect/PostgreSQL 상태가 `healthy`로 보이는지 확인
5. 발급된 token으로 protobuf ingest
6. Kafka publish
7. Kafka Connect sink
8. PostgreSQL row 적재 확인
9. `timestamp_ns` 보존 확인
10. `received_at`이 서버 수신 시각으로 별도 기록되는지 확인
11. `sequence`가 `2 -> 1 -> 0`처럼 꼬여 들어와도 각 row가 적재되는지 확인
12. `source_ip`가 유효한 IP 문자열로 저장되는지 확인
13. metric별 Kafka topic과 staging trigger를 거쳐 `device_metric_points`에 정규화 적재되는지 확인
14. `timestamp_ns`가 있으면 metric `event_time`이 장치 timestamp를 따르는지 확인
15. dimension table이 `device_id`, `boot_id`, `metric_key` 반복 저장을 줄이는지 확인
16. `device_metric_points`가 TimescaleDB hypertable이며 compression/retention policy job이 존재하는지 확인
17. nanopb mock이 만든 `signal_frame` payload를 ingest API가 수락하는지 확인
18. raw payload에 `signal_frame` compact JSON이 보존되는지 확인
19. `device.signal_frame.v1` topic과 staging trigger를 거쳐 `device_signal_frames`에 정규화 적재되는지 확인
20. `timestamp_ns`가 있으면 signal frame `event_time`이 장치 timestamp를 따르는지 확인
21. `signal_stream_definitions`가 stream metadata 반복 저장을 줄이는지 확인
22. `device_signal_frames`가 TimescaleDB hypertable이며 compression/retention policy job이 존재하는지 확인

### query-api coverage

현재 query-api test는 다음을 커버한다.

1. signal sample binary decode
2. interleaved/planar layout decode
3. scale/offset 적용
4. channel feature 통계 계산
5. unified stream 목록 응답
6. scalar stream series 조회
7. sampled stream series 조회
8. Redis-compatible cache hit 경로
9. sampled summary 요청 시 on-demand feature materialization 호출
10. scalar stream에 대한 raw frames 요청 거부
11. raw drill-down window 제한
12. invalid time range 거부
13. compose 기반 PostgreSQL/Redis/query-api 기동
14. 실제 `signal_frame_features` row 생성 확인
15. 실제 `BYTEA samples` decode 후 raw frame JSON 반환 확인
16. raw frame fallback이 sample point와 sample-bucket envelope를 반환하는지 확인

### stream-viewer frontend coverage

현재 frontend e2e는 다음을 커버한다.

1. mocked query-api URL을 `queryServerUrl` prop으로 전달
2. stream metadata 조회
3. sampled stream의 raw sample value chart 렌더링
4. scalar stream으로 전환
5. scalar stream chart 렌더링

### qemu_e2e coverage

현재 QEMU E2E는 다음을 커버한다.

1. ESP-IDF 6.0 project `set-target`
2. firmware build
3. QEMU monitor 실행
4. firmware 내부 nanopb encode 수행
5. UART hex-framed protobuf stream 추출
6. 추출한 bytes를 ingest API로 업로드
7. seeded device token 인증
8. Kafka publish
9. Kafka Connect sink
10. PostgreSQL row 적재 확인
11. `device_id`, `boot_id`, `sequence`, `timestamp_ns`, metric payload 보존 확인

### qemu_isr_enqueue coverage

QEMU ISR enqueue test는 다음을 커버한다.

1. ESP-IDF 6.0 project `set-target`
2. firmware build (aetus component 포함)
3. QEMU에서 `gptimer` ISR callback 실행
4. ISR context에서 `aetus_queue_item_t` struct stack frame 검증
5. `xQueueSendFromISR`으로 telemetry + status item enqueue
6. main task에서 queue drain 후 item 수신 확인
7. ISR 실행 후 stack high water mark가 256 bytes 이상 남았는지 확인
8. ISR stack 사용량이 4096 bytes 미만인지 확인

### hil firmware coverage

현재 HIL firmware의 compile coverage는 GitHub Actions에 포함한다.
실기기 flash/monitor/runtime 검증은 GitHub Actions 기본 테스트에 포함하지 않는다.

로컬 실기기에서 확인한 범위:

- ESP32-C5 build/flash/monitor
- Wi-Fi 접속
- portable `aetus` component 사용
- startup status event enqueue
- telemetry event enqueue
- double/int64/bool/string metric value encode
- optional HMAC-SHA256 auth mode
- random telemetry stream mode
- `/v1/ingest` HTTP POST
- backend E2E stack을 통한 PostgreSQL 적재

## 중요 구현 이력

최근 주요 커밋:

- `6637ddb` `Add HMAC ingest authentication`
- `d067f8b` `Add ESP32 HMAC upload mode`
- `77ec8af` `Document implemented HMAC auth path`
- `543cb1e` `Add normalized metric storage pipeline`
- `48e89c6` `Use TimescaleDB for metric storage`
- `4d1f437` `Split PostgreSQL base schema and Timescale layer`
- `c0e3d80` `Add ESP32-C5 random telemetry stream mode`
- `ec4bee8` `Downsample sampled streams by raw samples`

## 알려진 제약 / 주의사항

## 1. Admin 인증

`AETUS_ADMIN_PASSWORD` 환경변수로 admin page와 `/v1/control/*` JSON API의 비밀번호 기반 session 인증을 설정할 수 있다.

- 비밀번호가 설정되면 `POST /v1/control/login` (JSON) 또는 `POST /admin/login` (HTML form)으로 로그인
- 인증 성공 시 `aetus_admin_session` HttpOnly + SameSite=Strict 쿠키 발급 (기본 8시간 TTL)
- `/v1/control/status`, `/v1/control/devices`, `/v1/control/devices/issue` 는 유효한 session cookie 필요
- admin HTML page도 session cookie가 없으면 login form 표시
- `POST /v1/control/logout` 또는 `POST /admin/logout` 으로 session 제거
- `AETUS_ADMIN_PASSWORD` 미설정 시 기존처럼 인증 없이 동작 (하위 호환, 내부망 전제)
- `AETUS_ADMIN_SESSION_TTL_SECONDS`로 session TTL 조정 가능

## 2. Source IP는 환경 따라 다르게 보일 수 있음

로컬 e2e에서는 HTTP 클라이언트가 `127.0.0.1`에서 붙더라도, 컨테이너 내부 적재 값은 Docker bridge IP로 보일 수 있다.

즉 테스트는 특정 literal IP가 아니라 “유효한 IP 문자열인지”를 본다.

## 3. SQLite는 단일 control plane 전제

SQLite backend는 단일 Pod 또는 read-only seed 운영에 맞춘다.

현재 구현된 전환 경로:

- `ControlStore` interface
- `SQLite` / `PostgreSQL` backend 분기
- SQLite 주기 백업

남은 운영 보강:

- SQLite에서 PostgreSQL로 export/import하는 migration command

## 4. sequence 검증은 아직 하지 않음

현재 서버는 `sequence` monotonicity를 검사하지 않는다.

이유:

- 장치 재시도
- out-of-order 네트워크 도착
- raw ingest 우선

향후 필요하면 다음 레이어에서 다룬다.

- consumer 후처리
- 분석 파이프라인
- DB view 또는 materialized projection

## 5. mock-device build 시 보조 generator 타깃 이슈 가능

직접 `cmake --build`를 수동 호출할 때 로컬 환경에 따라 `protoc` 실행 파일을 찾지 못하는 경우가 있다.

현재 테스트 경로는 `grpc_tools.protoc` 기반 configure/build 흐름으로 정상 동작한다.

즉, 일반적인 개발 검증은 아래 명령을 우선 사용한다.

```bash
cd services/ingest-api
uv run pytest -q
```

## 다음 에이전트가 바로 보면 좋은 파일

- [[../services/ingest-api/src/aetus_ingest/app.py]]
- [[../services/ingest-api/src/aetus_ingest/control_db.py]]
- [[../services/ingest-api/src/aetus_ingest/control_status.py]]
- [[../services/ingest-api/src/aetus_ingest/publisher.py]]
- [[../services/ingest-api/src/aetus_ingest/normalize.py]]
- [[../services/ingest-api/tests/unit/test_ingest.py]]
- [[../services/ingest-api/tests/e2e/test_postgres_pipeline.py]]
- [[../services/ingest-api/tests/qemu_e2e/test_esp32_qemu_nanopb_pipeline.py]]
- [[../services/ingest-api/tests/helpers/nanopb_mock_device.py]]
- [[../services/mock-device-nanopb/src/mock_device_core.c]]
- [[../firmware/test-apps/qemu-telemetry/main/main.c]]
- [[../firmware/esp32-aetus/components/aetus/include/aetus.h]]
- [[../firmware/esp32-aetus/components/aetus/aetus.c]]
- [[../firmware/test-apps/esp32c5-upload-smoke/main/main.c]]
- [[../frontend/ingest-control-panel/src/IngestControlPanel.vue]]
- [[../frontend/stream-viewer/src/AetusStreamViewer.vue]]
- [[../services/query-api/tools/seed_dense_query_data.py]]
- [[../compose/e2e-compose.yml]]

## 추천 다음 작업

- query-api 인증 방식 결정
- query rollup background job 구현
- stream viewer zoom/drill-down UX 구현
- provisioning audit log 추가
- duplicate resend (`same device_id + boot_id + sequence`) E2E 추가
- FlashDB durable backlog 구현
- 대형 payload pointer/blob queue API 구현
