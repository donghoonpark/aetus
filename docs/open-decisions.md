# Open Decisions

## 지금 합의가 필요한 항목

### HMAC-SHA256 선택 인증 경로 도입 여부

현재 기본 인증은 장치별 정적 bearer token이다.

공개망 또는 보안 요구가 높은 배포를 고려해 `POST /v1/ingest`에 HMAC-SHA256 선택 인증 경로를 추가하는 안을 검토 중이다.

검토안:

- 기존 bearer token 경로는 유지한다.
- HMAC은 `POST /v1/ingest`에 우선 한정한다.
- `X-Device-Id`는 secret 조회를 위해 header에 둔다.
- `boot_id`, `sequence`는 protobuf body 내부 값을 사용하고 header에 반복하지 않는다.
- 장치와 서버는 raw protobuf body의 SHA256 hex digest를 각자 내부에서 계산한다.
- signature는 `prefix || body_sha256_hex`에 대해 계산한다.
- auth scheme/version은 `X-Aetus-Signature: hmac-sha256-v1=<hex>`의 좌변에 통합한다.
- `/v1/time`은 초기에는 기존 bearer token 인증을 유지한다.
- HMAC만으로 replay 방지는 완결되지 않으므로, replay guard는 별도 확장으로 둔다.

컨펌 필요 항목:

- HMAC을 실제 구현 범위에 포함할지
- HMAC mode를 bearer와 병행하는 `dual mode`로 둘지
- device token을 그대로 HMAC secret으로 재사용할지, 용어를 `device_secret`으로 바꿀지
- replay guard를 초기 범위에서 제외해도 되는지

## 참고

이미 확정된 항목은 본 노트에서 관리하지 않고 각 설계 문서 본문에 반영했다.

관련 문서:

- [[02-api]]
- [[03-protobuf]]
- [[04-data-pipeline-and-storage]]
- [[06-embedded-architecture]]
