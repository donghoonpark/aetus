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
- `POST /v1/ingest`
- `POST /v1/provision`
- `GET /admin/devices`
- `POST /admin/devices/issue`

### ingest 동작

`POST /v1/ingest`는 다음 순서로 처리된다.

1. `Content-Type=application/x-protobuf` 확인
2. source IP CIDR 확인
3. bearer token 추출
4. control DB에서 device token read-only 조회
5. protobuf 파싱
6. `device_id`, `boot_id`, `body` 기본 검증
7. in-memory rate limit 검사
8. 내부 event object로 normalize
9. memory publisher 또는 Kafka publisher로 publish

### 중요한 구현 결정

- ingest 경로는 `SQLite`에 write 하지 않는다.
- ingest 인증 조회는 `aiosqlite` 기반 read-only connection으로 수행한다.
- `timestamp_ns`는 장치 시각으로 별도 보존하고, `received_at`은 서버 수신 시각으로 별도 기록한다.
- `sequence` 순서가 꼬여 들어와도 ingest 레벨에서는 막지 않는다.

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

## 테스트 현황

테스트 실행 위치:

- [[../services/ingest-api]]

실행 명령:

```bash
uv run pytest -q
```

현재 통과 기준:

- `10 passed`

### unit coverage

현재 unit test는 다음을 커버한다.

- 정상 telemetry 업로드
- 정상 reboot status 업로드
- invalid token 거부
- provisioning 후 발급 token으로 ingest 가능
- `timestamp_ns` normalize 보존
- out-of-order `sequence` 허용
- admin page 브랜딩 렌더
- admin page pagination
- admin page search + copy token control 렌더

### e2e coverage

현재 e2e는 다음을 커버한다.

1. docker compose로 전체 스택 기동
2. `POST /v1/provision`으로 device token 발급
3. 발급된 token으로 protobuf ingest
4. Kafka publish
5. Kafka Connect sink
6. PostgreSQL row 적재 확인
7. `timestamp_ns` 보존 확인
8. `received_at`이 서버 수신 시각으로 별도 기록되는지 확인
9. `sequence`가 `2 -> 1 -> 0`처럼 꼬여 들어와도 각 row가 적재되는지 확인
10. `source_ip`가 유효한 IP 문자열로 저장되는지 확인

## 중요 구현 이력

최근 주요 커밋:

- `ad41195` `Restructure repo and add ingest service foundation`
- `3dbf7ac` `Finalize docs relocation into docs directory`
- `f5b2a0c` `Complete Kafka-to-Postgres e2e pipeline test`
- `eb48551` `Add SQLite-backed provisioning and async auth reads`
- `672f834` `Polish admin console with pagination and AETUS branding`
- `fd86ec7` `Expand admin tooling and ingest edge-case coverage`

## 알려진 제약 / 주의사항

## 1. Admin 인증 없음

현재 `/admin/devices`는 내부망 운영 도구 전제다.

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
- [[../services/ingest-api/src/aetus_ingest/publisher.py]]
- [[../services/ingest-api/src/aetus_ingest/normalize.py]]
- [[../services/ingest-api/tests/unit/test_ingest.py]]
- [[../services/ingest-api/tests/e2e/test_postgres_pipeline.py]]
- [[../services/ingest-api/tests/helpers/nanopb_mock_device.py]]
- [[../services/mock-device-nanopb/src/mock_device_core.c]]
- [[../compose/e2e-compose.yml]]

## 추천 다음 작업

- admin page 보호 방식 결정
- control DB backend abstraction
- provisioning audit log 추가
- duplicate resend (`same device_id + boot_id + sequence`) E2E 추가
- admin search/filter API 분리 여부 검토
