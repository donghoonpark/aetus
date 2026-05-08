# Embedded Data Collection Stack

## 읽는 순서

1. [[01-overview]]
2. [[02-api]]
3. [[03-protobuf]]
4. [[04-data-pipeline-and-storage]]
5. [[05-examples]]
6. [[06-embedded-architecture]]
7. [[06-1-event-driven-low-power-system-implementation-plan]]
8. [[06-2-standard-embedded-upload-stack]]
9. [[07-implementation-status]]
10. [[08-query-api-and-frontend]]
11. [[09-testing-and-e2e-coverage]]
12. [[10-client-packaging]]
13. [[open-decisions]]

## 문서 맵

- [[01-overview]]: 목표, 아키텍처, 역할 분리, 데이터 흐름
- [[02-api]]: ingest API 계약, 인증, 토큰 발급 API, 응답 정책
- [[03-protobuf]]: protobuf 구조, 중복 방지 키, 내부 이벤트 형태
- [[04-data-pipeline-and-storage]]: Kafka, PostgreSQL, Kubernetes, 운영/보안
- [[05-examples]]: FastAPI 예제 코드, nanopb 예제 코드, `.options` 예시
- [[06-embedded-architecture]]: ESP32-C5 임베디드 구조, task 분리, 큐잉, FlashDB, nanopb
- [[06-1-event-driven-low-power-system-implementation-plan]]: OPT3001 기반 이벤트 구동 저전력 전략, 전력 예산, 배터리 수명 계산
- [[06-2-standard-embedded-upload-stack]]: `firmware/esp32-aetus` 표준 업로드 컴포넌트, 공개 API, thread safety, 예제 app, HIL 소비 구조
- [[07-implementation-status]]: 현재 코드 구현 범위, 테스트 커버리지, 운영 제약, 다음 작업 포인트
- [[08-query-api-and-frontend]]: signal query API, downsampling, 표준 프론트엔드 컴포넌트, 시각화 서비스 분리 방안
- [[09-testing-and-e2e-coverage]]: 펌웨어, ingest, Kafka, DB, query-api, frontend까지의 검증 루프와 남은 테스트 구멍
- [[10-client-packaging]]: Python/Rust ingest client의 PyPI/crates.io 배포 준비, 검증, 릴리스 절차
- [[open-decisions]]: 아직 합의가 필요한 기술 결정사항

## 빠른 요약

- 디바이스 업로드 포맷은 `protobuf`
- 서버 진입점은 `FastAPI`
- FastAPI는 protobuf를 내부 표준 object로 정규화
- Kafka에는 운영 친화적인 JSON 이벤트를 publish
- PostgreSQL 적재는 `Kafka Connect JDBC Sink` 중심이며, raw 이벤트 테이블과 장기 metric point/signal frame 테이블을 분리 저장
- 개발 DB는 TimescaleDB 이미지를 사용하고, `device_metric_points`와 `device_signal_frames`는 선택 Timescale layer에서 hypertable/compression/retention을 적용한다
- 장기 metric/signal 보관은 `devices`, `device_boot_sessions`, `metric_definitions`, `signal_stream_definitions` dimension key를 통해 문자열 반복을 줄임
- `device_id + boot_id + sequence`를 기본 중복 방지 키로 사용
- `sequence`는 각 부팅 세션마다 `0`부터 시작
- Kafka와 PostgreSQL은 분리망 내 self-managed 운영 전제
- 임베디드 표준 스택은 `ESP-IDF + FreeRTOS + NimBLE + FlashDB + nanopb`
- 표준 업로드 컴포넌트는 `firmware/esp32-aetus`에 위치하며 유저 task는 thread-safe enqueue API만 호출한다
- `firmware/examples`는 표준 컴포넌트를 실제 ESP-IDF app으로 소비하는 빌드 가능한 예제를 제공한다
- signal visualization은 ingest API와 분리된 `query-api`에서 고정 `x4` rollup tier, `Redis` cache, `JSON + gzip/br` 압축 응답을 사용하는 방향을 기본안으로 둔다
- 조회용 표준 프론트엔드는 `frontend/stream-viewer`의 `@aetus/stream-viewer` Vue 컴포넌트로 제공하며, query-api URL만 지정해 이식할 수 있다
- bootstrap token은 단일 공용 token이며 유출/공유를 전제로 매우 가혹한 제한만 둔다
- provisioning allowlist는 `source IP + hardware_id` 기준으로 FastAPI에서 관리한다
- HMAC-SHA256 ingest 인증은 bearer token과 병행하는 선택 경로로 구현되어 있다

## 원본 문서

- [[architecture-draft]]
