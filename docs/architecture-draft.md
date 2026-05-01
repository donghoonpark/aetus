# Archived Architecture Draft

이 파일은 초기 통합 초안을 보존하기 위한 archive note다.

현재 구현과 운영 기준은 아래 분할 문서를 기준으로 본다.

- [[00-index]]
- [[01-overview]]
- [[02-api]]
- [[03-protobuf]]
- [[04-data-pipeline-and-storage]]
- [[05-examples]]
- [[06-embedded-architecture]]
- [[06-1-event-driven-low-power-system-implementation-plan]]
- [[06-2-standard-embedded-upload-stack]]
- [[07-implementation-status]]
- [[open-decisions]]

초기 초안에는 다음과 같이 현재 구현과 다른 내용이 섞여 있었다.

- `boot_id`를 선택 필드처럼 다루던 내용
- `device_id + sequence`만으로 중복 방지를 시작하자는 내용
- 장치군 공통 token을 후보로 둔 내용
- HTTPS를 기본 권장으로 두던 내용
- managed Kafka 가능성을 전제로 둔 내용
- raw JSON 중심 장기 적재 후보

현재 확정 구현은 `boot_id` 필수, `device_id + boot_id + sequence` 기준 upsert, 장치별 bearer/HMAC credential, HTTP 기본 transport, self-managed Kafka/PostgreSQL, normalized metric point 적재, TimescaleDB optional layer를 따른다.
