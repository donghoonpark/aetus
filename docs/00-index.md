# Embedded Data Collection Stack

## 읽는 순서

1. [[01-overview]]
2. [[02-api]]
3. [[03-protobuf]]
4. [[04-data-pipeline-and-storage]]
5. [[05-examples]]
6. [[06-embedded-architecture]]
7. [[06-1-event-driven-low-power-system-implementation-plan]]
8. [[open-decisions]]

## 문서 맵

- [[01-overview]]: 목표, 아키텍처, 역할 분리, 데이터 흐름
- [[02-api]]: ingest API 계약, 인증, 토큰 발급 API, 응답 정책
- [[03-protobuf]]: protobuf 구조, 중복 방지 키, 내부 이벤트 형태
- [[04-data-pipeline-and-storage]]: Kafka, PostgreSQL, Kubernetes, 운영/보안
- [[05-examples]]: FastAPI 예제 코드, nanopb 예제 코드, `.options` 예시
- [[06-embedded-architecture]]: ESP32-C5 임베디드 구조, task 분리, 큐잉, FlashDB, nanopb
- [[06-1-event-driven-low-power-system-implementation-plan]]: OPT3001 기반 이벤트 구동 저전력 전략, 전력 예산, 배터리 수명 계산
- [[open-decisions]]: 아직 합의가 필요한 기술 결정사항

## 빠른 요약

- 디바이스 업로드 포맷은 `protobuf`
- 서버 진입점은 `FastAPI`
- FastAPI는 protobuf를 내부 표준 object로 정규화
- Kafka에는 운영 친화적인 JSON 이벤트를 publish
- PostgreSQL 적재는 `Kafka Connect JDBC Sink` 중심
- `device_id + boot_id + sequence`를 기본 중복 방지 키로 사용
- `sequence`는 각 부팅 세션마다 `0`부터 시작
- Kafka와 PostgreSQL은 분리망 내 self-managed 운영 전제
- 임베디드 표준 스택은 `ESP-IDF + FreeRTOS + NimBLE + FlashDB + nanopb`
- bootstrap token은 단일 공용 token이며 유출/공유를 전제로 매우 가혹한 제한만 둔다
- provisioning allowlist는 `source IP + hardware_id` 기준으로 FastAPI에서 관리한다

## 원본 문서

- [[architecture-draft]]
