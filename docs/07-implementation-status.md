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
compose/
firmware/
services/
  ingest-api/
  kafka/
  kafka-connect/
  postgres/
  mock-device-nanopb/
```

### 핵심 경계

- `services/ingest-api`
  - FastAPI 기반 ingest/provisioning/control plane
- `services/kafka`
  - self-managed Kafka 브로커 이미지
- `services/kafka-connect`
  - JDBC Sink 기반 PostgreSQL 적재
- `services/postgres`
  - raw event 저장용 PostgreSQL
- `services/mock-device-nanopb`
  - CMake + FetchContent + nanopb + pybind11 기반 mock device
- `firmware/esp32-qemu-telemetry`
  - ESP-IDF 6.0 + nanopb 기반 QEMU E2E 전용 firmware stream generator
- `firmware/esp32-aetus`
  - ESP-IDF portable upload stack component
- `firmware/esp32c5-upload-smoke`
  - `firmware/esp32-aetus`를 소비하는 ESP32-C5 HIL app
- `compose/e2e-compose.yml`
  - 전체 파이프라인 E2E 실행용 compose

## 구현 완료 범위

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
4. bearer token 추출
5. control DB에서 device token read-only 조회
6. protobuf 파싱
7. `device_id`, `boot_id`, `body` 기본 검증
8. 내부 event object로 normalize
9. memory publisher 또는 Kafka publisher로 publish

현재 구현은 bearer token 인증만 지원한다.

HMAC-SHA256 선택 인증 경로를 추가할 경우 처리 순서는 다음처럼 바뀐다.

1. `Content-Type=application/x-protobuf` 확인
2. source IP CIDR 확인
3. in-memory rate limit 검사
4. request body 읽기 및 크기 제한 확인
5. bearer 또는 HMAC 인증 방식 판별
6. bearer mode는 기존 device token 비교
7. HMAC mode는 raw body SHA256 검증 후 body hash 기반 signature 검증
8. protobuf 파싱
9. body 내부 `device_id`, `boot_id`, `body` 기본 검증
10. 내부 event object로 normalize
11. memory publisher 또는 Kafka publisher로 publish

HMAC 경로는 아직 구현하지 않았으며, 설계 컨펌 후 작업한다.

### RTC time sync 동작

`GET /v1/time`은 장치별 정적 bearer token으로 인증하고 서버 시간을 반환한다.

- `unix_time_ns`는 JSON 정밀도 손실을 피하기 위해 문자열로 반환한다.
- 응답에는 `Cache-Control: no-store`가 붙는다.
- source IP CIDR, device token 검증, in-memory rate limit는 ingest 계열과 동일하게 적용한다.
- ESP32 표준 컴포넌트는 이 값을 받아 `settimeofday()`로 RTC를 설정하고, 이후 `timestamp_ns` helper로 telemetry/status에 시간을 채운다.

### 중요한 구현 결정

- ingest 경로는 `SQLite`에 write 하지 않는다.
- ingest 인증 조회는 `aiosqlite` 기반 read-only connection으로 수행한다.
- `timestamp_ns`는 장치 시각으로 별도 보존하고, `received_at`은 서버 수신 시각으로 별도 기록한다.
- `sequence` 순서가 꼬여 들어와도 ingest 레벨에서는 막지 않는다.
- HMAC 인증을 추가하더라도 초기 구현에서는 ingest 경로 DB write 원칙을 유지하고, replay guard는 별도 확장으로 둔다.

## 2. Provisioning / Control Plane

구현 파일:

- [[../services/ingest-api/src/aetus_ingest/control_db.py]]
- [[../services/ingest-api/src/aetus_ingest/schemas.py]]

현재 control DB는 `SQLite`를 사용한다.

역할:

- hardware allowlist 저장
- device token 저장
- provisioning 시 신규 token 발급
- admin page용 device list 조회
- control panel용 JSON API 제공

### SQLite 사용 방식

- read path: `aiosqlite`
- write path: provisioning / admin issue only
- 설정:
  - `journal_mode=WAL`
  - `synchronous=NORMAL`
  - `busy_timeout=3000`

### 현재 전환 기준

문서상 운영 가정은 다음과 같다.

- 초기 운용: `SQLite`
- `~1k req/s` 근방: `MySQL + multi-pod` 전환 검토

코드에는 아직 MySQL abstraction은 없다.
현재는 `ControlDB`가 SQLite에 직접 묶여 있다.

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

## 4. Kafka / Kafka Connect / PostgreSQL

구현 파일:

- [[../compose/e2e-compose.yml]]
- [[../services/kafka-connect/sink-config.json]]
- [[../services/postgres/init.sql]]

현재 적재 흐름:

`FastAPI -> Kafka -> Kafka Connect JDBC Sink -> PostgreSQL`

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

- [[../firmware/esp32-qemu-telemetry]]
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

## 7. ESP32 AETUS Portable Upload Stack

구현 위치:

- [[../firmware/esp32-aetus]]
- [[../firmware/esp32-aetus/components/aetus/include/aetus.h]]
- [[../firmware/examples]]
- [[../firmware/esp32c5-upload-smoke]]

목적:

- ESP32-C5 제품 firmware에서 공통으로 쓸 업로드 스택을 제공한다.
- 유저 비즈니스 로직은 thread-safe enqueue API만 호출한다.
- 별도 uploader task가 queue, upload timer, Wi-Fi, nanopb encode, HTTP POST를 담당한다.

현재 공개 API:

- `aetus_start`
- `aetus_sync_rtc`
- `aetus_rtc_timestamp_ns`
- `aetus_telemetry_set_timestamp_rtc`
- `aetus_status_set_timestamp_rtc`
- `aetus_enqueue_telemetry`
- `aetus_enqueue_status`
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

예제 app:

- `firmware/examples/basic-telemetry`: 최소 telemetry/status enqueue 예제
- `firmware/examples/multitask-producers`: 여러 FreeRTOS producer task에서 enqueue하는 예제
- `firmware/examples/metric-types`: int64/double/bool/string/bytes metric type 예제
- 모든 예제는 ESP32-C5, ESP-IDF 6.0, 4MB flash, 3MB factory app partition 기준으로 빌드한다.

현재 미구현:

- FlashDB durable backlog
- 대형 payload용 pointer/blob queue API
- ISR-safe enqueue API
- Wi-Fi ownership adapter
- HTTPS certificate verification bypass option

## 테스트 현황

테스트 실행 위치:

- [[../services/ingest-api]]

실행 명령:

```bash
uv run pytest -q
```

현재 통과 기준:

- 일반 unit/e2e: `35 passed`
- QEMU e2e: 기본 실행에서는 skip, `AETUS_RUN_QEMU_E2E=1`일 때 별도 실행

### unit coverage

현재 unit test는 다음을 커버한다.

- 정상 telemetry 업로드
- 정상 reboot status 업로드
- invalid token 거부
- provisioning 후 발급 token으로 ingest 가능
- control JSON API 기반 token 발급/조회
- control status endpoint component state 확인
- `timestamp_ns` normalize 보존
- out-of-order `sequence` 허용
- admin page 브랜딩 렌더
- admin page pagination
- admin page search + copy token control 렌더

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

### hil firmware coverage

현재 HIL firmware는 GitHub Actions 기본 테스트에 포함하지 않는다.

로컬 실기기에서 확인한 범위:

- ESP32-C5 build/flash/monitor
- Wi-Fi 접속
- portable `aetus` component 사용
- startup status event enqueue
- telemetry event enqueue
- double/int64/bool/string metric value encode
- `/v1/ingest` HTTP POST
- backend E2E stack을 통한 PostgreSQL 적재

## 중요 구현 이력

최근 주요 커밋:

- `ad41195` `Restructure repo and add ingest service foundation`
- `3dbf7ac` `Finalize docs relocation into docs directory`
- `f5b2a0c` `Complete Kafka-to-Postgres e2e pipeline test`
- `eb48551` `Add SQLite-backed provisioning and async auth reads`
- `672f834` `Polish admin console with pagination and AETUS branding`
- `fd86ec7` `Expand admin tooling and ingest edge-case coverage`
- `HEAD` 이후: Vue/Naive UI control panel, control status API, JSON device APIs 추가

## 알려진 제약 / 주의사항

## 1. Admin 인증 없음

현재 `/admin/devices`는 내부망 운영 도구 전제다.

`/v1/control/*` JSON API도 현재 같은 전제다.

필요시 다음 중 하나를 추가해야 한다.

- reverse proxy basic auth
- 별도 admin bearer token
- 사설망 접근 제어

## 2. Source IP는 환경 따라 다르게 보일 수 있음

로컬 e2e에서는 HTTP 클라이언트가 `127.0.0.1`에서 붙더라도, 컨테이너 내부 적재 값은 Docker bridge IP로 보일 수 있다.

즉 테스트는 특정 literal IP가 아니라 “유효한 IP 문자열인지”를 본다.

## 3. SQLite는 아직 단일 control plane 전제

현재는 replica 분산이나 shared DB abstraction이 없다.

다음 단계에서 고려할 수 있는 것:

- `ControlDB` interface 분리
- `SQLite` / `MySQL` backend 분기
- control plane write API 별도 분리

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
- [[../firmware/esp32-qemu-telemetry/main/main.c]]
- [[../firmware/esp32-aetus/components/aetus/include/aetus.h]]
- [[../firmware/esp32-aetus/components/aetus/aetus.c]]
- [[../firmware/esp32c5-upload-smoke/main/main.c]]
- [[../frontend/ingest-control-panel/src/IngestControlPanel.vue]]
- [[../compose/e2e-compose.yml]]

## 추천 다음 작업

- admin page 보호 방식 결정
- control DB backend abstraction
- control panel 인증/배포 방식 결정
- provisioning audit log 추가
- duplicate resend (`same device_id + boot_id + sequence`) E2E 추가
