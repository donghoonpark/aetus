# Open Decisions

## 지금 합의가 필요한 항목

signal query / visualization 영역에서 아래 항목이 열려 있다.

1. query-api 인증을 최종적으로 내부망 세션 중심으로 둘지, 별도 read-only token도 둘지

HMAC-SHA256 ingest 인증 경로는 `dual mode`로 구현 완료했다. bearer token 경로는 유지하고, HMAC mode는 `POST /v1/ingest`에만 적용한다. `/v1/time`은 bearer token 인증을 유지한다.

replay guard는 초기 범위에서 제외하고, downstream의 `device_id + boot_id + sequence` 중복 식별 정책을 유지한다.

`signal_frame_features` on-demand materialization 시 raw `BYTEA samples` decode는 query-api 런타임에서 수행한다. PostgreSQL은 raw 저장, 범위 조회, feature/rollup upsert, retention을 맡고, binary format 해석과 feature 계산은 애플리케이션 코드에서 관리한다.

## 참고

이미 확정된 항목은 본 노트에서 관리하지 않고 각 설계 문서 본문에 반영했다.

관련 문서:

- [[02-api]]
- [[03-protobuf]]
- [[04-data-pipeline-and-storage]]
- [[06-embedded-architecture]]
- [[06-2-standard-embedded-upload-stack]]
- [[07-implementation-status]]
- [[08-query-api-and-frontend]]
