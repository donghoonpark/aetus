# Open Decisions

## 현재 상태

공개 저장소 전환 기준으로 release-blocking open decision은 없다.

확정된 결정은 각 설계 문서 본문에 반영하고, 이 파일에는 다음 릴리스 이후 검토할 수 있는 post-0.1 항목만 남긴다.

## Post-0.1 검토 항목

- Query API JWT는 현재 `HS256` shared secret 방식으로 구현되어 있다. 공개망, SSO, 다중 issuer 연동이 필요해지면 `RS256`/`ES256` + JWKS 검증 경로를 추가한다.
- Query API 권한은 현재 JWT claim의 `devices`, `streams`, `scope`, `max_range_seconds`, `max_points`를 기준으로 한다. 조직/사이트 단위 권한 모델이 필요해지면 control DB 또는 별도 identity source와 연결한다.
- Stream viewer token 만료 처리는 `authToken`/`tokenProvider` props와 host application 책임으로 둔다. 더 강한 UX가 필요하면 refresh lifecycle event를 확장한다.
- `signal_frame_features` on-demand materialization 시 raw `BYTEA samples` decode는 query-api 런타임에서 수행한다. PostgreSQL은 raw 저장, 범위 조회, feature/rollup upsert, retention을 맡고, binary format 해석과 feature 계산은 애플리케이션 코드에서 관리한다.

관련 문서:

- [[02-api]]
- [[03-protobuf]]
- [[04-data-pipeline-and-storage]]
- [[06-embedded-architecture]]
- [[06-2-standard-embedded-upload-stack]]
- [[07-implementation-status]]
- [[08-query-api-and-frontend]]
