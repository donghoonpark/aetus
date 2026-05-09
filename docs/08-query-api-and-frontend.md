# Query API And Frontend

## 목적

이 문서는 `device_metric_points`와 `device_signal_frames`에 저장되는 시계열 데이터를
운영 서비스에서 어떻게 조회하고 시각화할지 정리한다.

핵심 방향:

- ingest API와 query API는 분리한다
- 내부 저장 모델과 공개 조회 모델을 분리한다
- 사용자와 프론트엔드에는 `metric`과 `signal frame` 대신 `stream` 개념을 우선 노출한다
- raw frame 저장과 시각화용 조회 모델을 분리한다
- 브라우저는 chart renderer 역할에 집중하고, downsampling은 서버가 담당한다

## 왜 별도 query-api가 필요한가

ingest API와 stream query는 부하 특성이 다르다.

| 항목 | ingest API | query API |
| --- | --- | --- |
| 트래픽 성격 | write-heavy | read-heavy |
| 지연 요구 | 짧고 일정해야 함 | 사용자 인터랙션에 따라 burst 발생 |
| 주요 작업 | auth, protobuf decode, Kafka publish | time range 조회, frame stitch, downsampling, cache |
| scale 기준 | 초당 요청 수, publish latency | 동시 사용자 수, chart density, cache hit ratio |
| 장애 영향 | 수집 중단 | 조회 지연 또는 일부 화면 저하 |

따라서 `services/ingest-api`와 `services/query-api`를 분리하고,
`k8s`에서도 별도 deployment로 운영하는 방향을 기본안으로 둔다.

## 상위 구조

```mermaid
flowchart TB
    Device["Embedded devices"] --> Ingest["ingest-api"]
    Ingest --> Kafka["Kafka"]
    Kafka --> Connect["Kafka Connect"]
    Connect --> PG["PostgreSQL / TimescaleDB"]

    PG --> Query["query-api"]
    Query --> Cache["Redis cache"]
    Query --> FE["Standard frontend"]
    FE --> User["Operator / dashboard"]
```

## 용어 정리

- `server-side rendering`:
  HTML이나 이미지 자체를 서버에서 그려 보내는 의미로는 사용하지 않는다.
- `server-side downsampling`:
  query-api가 시간 범위와 화면 해상도에 맞춰 scalar metric 또는 sampled signal을 chart용 데이터셋으로 축약해 반환하는 방식을 의미한다.

## 공개 조회 모델

query-api는 내부 저장 구조인 `metric point`와 `signal frame`를 사용자에게 직접 노출하지 않는다.

기본 원칙:

- 공개 API의 주 개념은 `stream`
- 사용자 입장에서는 온도, 배터리, 진동, 파형 모두 `stream`으로 보이게 한다
- 내부적으로는 `metric`과 `signal frame`를 계속 분리 저장하고 최적화한다
- 응답 metadata에는 시각화 힌트 수준의 `kind`는 둘 수 있다

권장 `stream kind`:

- `scalar`: 저주기 또는 sparse metric series
- `sampled`: dense sampled signal series

즉, query-api는 “저장 포맷”이 아니라 “조회 가능한 시계열”을 중심으로 계약을 잡는다.

```mermaid
flowchart TB
    MetricStore["device_metric_points"] --> StreamModel["logical stream model"]
    SignalStore["device_signal_frames"] --> StreamModel
    StreamModel --> Query["query-api"]
    Query --> UI["operator UI / charts"]
```

## protobuf와 query layer의 네이밍 규칙

protobuf 내부에서는 이름을 완전히 통합하지 않는다.

- `Metric.key`: 개별 scalar 항목 이름
- `SignalFrame.stream_key`: sampled signal 묶음 이름

이 구분은 protobuf 구조를 읽을 때 의미를 더 잘 드러낸다.

대신 query-api에서는 둘을 공통의 logical identifier로 취급한다.

- metric의 `Metric.key` -> query layer의 `stream.key`
- signal의 `SignalFrame.stream_key` -> query layer의 `stream.key`

향후 새로운 payload 타입이 추가되면 권장 규칙은 다음과 같다.

- item-level field는 `key`
- stream-level field는 `stream_key`
- query-api에서는 둘 다 통합해 `stream.key`로 노출

이 문서에서 필요한 것은 대부분 `server-side downsampling`이다.

## 조회 계층 기본 원칙

### raw frame 직접 조회를 기본 경로로 두지 않는다

`device_signal_frames.samples`는 forensic, export, 재처리의 기준 원본으로 보관한다.
하지만 대시보드가 항상 raw frame을 읽어 차트를 그리게 하면:

- DB read amplification이 커지고
- API 서버가 매 요청마다 decode 비용을 내며
- 브라우저가 너무 많은 점을 그리게 된다

따라서 기본 조회 모델은 아래 3계층으로 둔다.

1. `raw frames`
2. `rollups`
3. `features (on-demand cache)`

```mermaid
flowchart TD
    Raw --> RollupJob["downsample / rollup job"]
    Raw --> FeatureJob["on-demand feature materializer"]
    RollupJob --> Rollups["signal_rollup_points"]
    Raw --> Drilldown["narrow raw drill-down"]
    FeatureJob --> Features["signal_frame_features"]
    Features --> Query["query-api"]
    Rollups --> Query
    Drilldown --> Query
```

## 권장 저장 모델

### 1. Raw frame

현재 구현을 유지한다.

- 테이블: `device_signal_frames`
- 단위: `1 row = 1 signal frame`
- 용도: 재처리, 원본 확인, 좁은 구간 drill-down, 외부 export

### 2. Feature table

프레임 단위 품질/통계를 별도로 저장한다.

중요:

- `signal_frame_features`는 eager precompute 테이블이 아니다
- 첫 query에서 필요해질 때만 생성하는 `on-demand materialized cache`로 둔다
- source raw retention 또는 별도 feature retention을 넘기면 자동 삭제한다
- rollup 생성의 필수 입력으로 두지 않는다

권장 예:

- `min`
- `max`
- `avg`
- `rms`
- `stddev`
- `peak_abs`
- `sample_count`
- `duration_ns`
- `missing_sample_count`
- `quality_flags`

용도:

- 이벤트 탐지
- threshold 검색
- 구간 목록
- operator용 요약 테이블

생성 정책:

- `summary`, `search`, `event list` 같은 query에서 필요한 범위만 생성
- query miss 시 즉시 raw에서 계산하거나, 짧은 동기 계산 후 캐시 저장
- 같은 범위의 반복 조회는 `signal_frame_features`와 `Redis` cache가 흡수

보관 정책:

- `signal_frame_features`는 파생 데이터이므로 원본보다 짧은 retention을 둘 수 있다
- 최소 원칙은 “의미 있는 query 재사용 구간까지만 보관”
- 예: `7일`, `30일`, `90일` 중 하나로 운영하고 만료 후 재생성 허용

### 3. Rollup table

시각화용으로 미리 축약한 stream series를 저장한다.

권장 해상도 계층은 `1초`를 중심으로 한 고정 `x4` 배율로 둔다.

예시:

- `62.5ms`
- `250ms`
- `1s`
- `4s`
- `16s`
- `64s`
- `256s`
- `1024s`

권장 표현:

- `min/max envelope`
- 필요 시 `avg`

이유:

- line chart는 한 bucket에 평균 하나만 두면 peak가 사라질 수 있다
- `min/max` 쌍은 화면상 변동 폭을 유지하기 좋다

이 계층의 장점:

- query-api가 임의 해상도를 직접 만들지 않고 가까운 고정 tier로 정규화할 수 있다
- 같은 요청이 같은 cache key로 수렴해 cache hit rate가 좋아진다
- 프론트 줌 단계와 서버 rollup 단계가 예측 가능해진다
- retention과 precompute 범위를 tier별로 관리하기 쉽다

주의:

- 모든 stream에 모든 tier를 물리적으로 다 만들 필요는 없다
- sample rate가 낮은 stream은 더 미세한 tier를 생략할 수 있다
- 하지만 query contract는 공통 tier ladder를 사용해도 된다

```mermaid
flowchart TB
    T0["62.5ms"] --> T1["250ms"]
    T1 --> T2["1s"]
    T2 --> T3["4s"]
    T3 --> T4["16s"]
    T4 --> T5["64s"]
    T5 --> T6["256s"]
    T6 --> T7["1024s"]
```

## query-api의 역할

### 범위

- stream metadata 조회
- scalar stream 시계열 조회
- sampled stream의 feature 조회와 on-demand materialization
- sampled stream의 rollup 조회
- sampled stream의 좁은 구간 raw drill-down 조회
- 화면 폭 기반 downsampling
- 캐시
- 조회용 인증/인가

### 비범위

- ingest
- device provisioning
- Kafka publish
- raw frame 장기 아카이브 정책

## 표준 API shape 제안

초기에는 REST가 가장 단순하다.

권장 엔드포인트:

- `GET /v1/query/devices/{device_id}/streams`
- `GET /v1/query/devices/{device_id}/streams/{key}/summary`
- `GET /v1/query/devices/{device_id}/streams/{key}/series`
- `GET /v1/query/devices/{device_id}/streams/{key}/frames`

### stream 목록

`GET /v1/query/devices/{device_id}/streams`

응답 예시:

```json
{
  "device_id": "esp32c5-test-001",
  "streams": [
    {
      "key": "cpp.accel.demo",
      "kind": "sampled",
      "unit": "g",
      "nominal_rate_hz": 200,
      "encoding": "float32_le",
      "layout": "interleaved",
      "channels": ["ax", "ay", "az"],
      "latest_event_time": "2026-05-03T00:10:00Z"
    },
    {
      "key": "temperature",
      "kind": "scalar",
      "unit": "celsius",
      "value_type": "double",
      "latest_event_time": "2026-05-03T00:10:01Z"
    }
  ]
}
```

### summary 조회

`GET /v1/query/devices/{device_id}/streams/{key}/summary?from=...&to=...`

응답 예시:

```json
{
  "device_id": "esp32c5-test-001",
  "key": "cpp.accel.demo",
  "kind": "sampled",
  "from": "2026-05-03T00:00:00Z",
  "to": "2026-05-03T01:00:00Z",
  "frame_count": 120,
  "sample_count": 24000,
  "duration_ns": "60000000000",
  "features": {
    "ax": {
      "min": -0.91,
      "max": 0.88,
      "avg": 0.02,
      "rms": 0.31
    }
  }
}
```

### chart series 조회

`GET /v1/query/devices/{device_id}/streams/{key}/series?from=...&to=...&max_points=1500`

핵심 규칙:

- 프론트는 원하는 화면 폭에 비례한 `max_points`만 전달한다
- query-api는 요청 해상도를 고정 `x4` rollup tier 중 하나로 정규화한다
- query-api는 stream kind에 따라 metric/raw/rollup 중 적절한 소스를 선택한다
- 응답 점 수는 `max_points`를 크게 넘기지 않는다
- 응답은 기본적으로 JSON을 사용하고, `Accept-Encoding` 기반 압축을 적용한다

응답 예시:

```json
{
  "device_id": "esp32c5-test-001",
  "key": "cpp.accel.demo",
  "kind": "sampled",
  "resolution": "10s",
  "mode": "envelope",
  "channels": [
    {
      "name": "ax",
      "points": [
        {
          "ts": "2026-05-03T00:00:00Z",
          "min": -0.80,
          "max": 0.77
        }
      ]
    }
  ]
}
```

scalar stream 예시:

```json
{
  "device_id": "esp32c5-test-001",
  "key": "temperature",
  "kind": "scalar",
  "value_type": "double",
  "resolution": "1s",
  "points": [
    {
      "ts": "2026-05-03T00:00:00Z",
      "value": 23.75
    }
  ]
}
```

string scalar stream 예시:

```json
{
  "device_id": "esp32c5-test-001",
  "key": "machine.state",
  "kind": "scalar",
  "value_type": "string",
  "resolution": "raw",
  "points": [
    {
      "ts": "2026-05-03T00:00:00Z",
      "text": "warming"
    }
  ]
}
```

### raw frame drill-down 조회

`GET /v1/query/devices/{device_id}/streams/{key}/frames?from=...&to=...`

제약:

- `kind=sampled` stream에만 허용
- 아주 좁은 구간에서만 허용
- 기본 `max_duration` 제한 필요
- 응답은 운영자 export나 상세 디버깅 용도
- 기본 응답은 JSON을 사용하고, 큰 응답은 압축을 적용한다

## source 선택 규칙

query-api는 같은 엔드포인트라도 기간과 `max_points`, 그리고 stream kind에 따라 소스를 바꾼다.
그리고 내부적으로는 요청 해상도를 고정 tier로 반올림하고 `from/to`를 bucket 경계에 정렬한다.

```mermaid
flowchart TD
    Req["series request"] --> Kind{"stream kind?"}
    Kind -- "scalar" --> Scalar["read metric series"]
    Kind -- "sampled" --> Width{"expected points > max_points?"}
    Width -- "no" --> Raw["read raw frames and return decoded samples"]
    Width -- "yes" --> Range{"matching rollup exists?"}
    Range -- "yes" --> Rollup["read rollup table"]
    Range -- "no" --> Batch["read raw frames and build sample-bucket envelope"]
    Scalar --> Resp["return chart dataset"]
    Raw --> Resp
    Rollup --> Resp
    Batch --> Resp
```

권장 규칙:

- 요청 해상도는 `requested_span / max_points`로 계산
- 계산된 해상도보다 작지 않은 가장 가까운 `x4` tier를 선택
- `from/to`는 선택된 tier 경계에 맞춰 정렬
- 아주 짧은 구간은 raw frame을 decode해 sample point를 직접 반환할 수 있다
- rollup이 없는 구간은 frame 단위가 아니라 raw sample timeline을 기준으로 `max_points`개 bucket envelope를 만든다

예시:

- `3s / 1200pt` 요청은 raw 또는 `62.5ms` tier 후보
- `15m / 1200pt` 요청은 `1s` 또는 `4s` tier 후보
- `6h / 1500pt` 요청은 `16s` tier 후보
- `3d / 1200pt` 요청은 `256s` tier 후보

현재 구현된 raw fallback:

- `source_sample_count <= max_points`이면 `mode=samples`, `resolution=raw-sample`로 channel별 `value` point를 반환한다
- `source_sample_count > max_points`이면 `mode=envelope`, `resolution=raw-sample-bucket`으로 정확히 `max_points`개 이하의 min/max/avg bucket을 반환한다
- 예: 270Hz급 signal의 최근 10분 요청에서 `max_points=10000`이면 약 16만 원본 sample을 읽고 10,000개 bucket point로 응답한다

### feature query 경로

`summary`나 event search 같은 요청은 아래 경로를 따른다.

```mermaid
flowchart TD
    Req["feature/summary request"] --> Hit{"feature rows exist?"}
    Hit -- "yes" --> Read["read signal_frame_features"]
    Hit -- "no" --> Build["compute from raw frames for requested window"]
    Build --> Persist["upsert signal_frame_features"]
    Read --> Resp["return summary/search result"]
    Persist --> Resp
```

이렇게 두는 이유:

- 사용되지 않는 stream/window에 대해 feature를 미리 쌓지 않아도 된다
- feature 저장소가 장기 누적되어 raw와 비슷한 부하를 다시 만들지 않는다
- query가 실제로 발생한 범위만 materialize하므로 저장량 예측이 쉽다

## downsampling 알고리즘

초기 권장 순서:

1. `min/max envelope`
2. 필요 시 `avg`
3. 아주 넓은 기간의 개요 차트에서만 `LTTB` 보조 검토

초기 기본값으로 `min/max envelope`를 권장하는 이유:

- peak 보존이 좋다
- 구현이 단순하다
- 채널별 독립 처리에 유리하다
- operator 차트에서 의미가 명확하다

`LTTB`는 시각적으로 부드러울 수 있지만:

- 계산이 더 복잡하고
- channel별 peak 보존 요구와 다를 수 있으며
- envelope와 같이 쓰지 않으면 이상치가 약해질 수 있다

따라서 v1은 `envelope first`가 적합하다.

## rollup 생성 경로

rollup 생성은 `DB 내부 job`을 기본안으로 둔다.

권장 방식:

- `signal_rollup_points`는 PostgreSQL/TimescaleDB 내부 job이 주기적으로 생성
- 구현은 `stored procedure + Timescale background job` 또는 `pg_cron` 기반으로 둔다
- rollup은 raw frame 또는 하위 tier rollup을 읽어 상위 tier를 만들게 한다

권장 이유:

- query-api와 생성 job의 책임이 분리된다
- rollup 결과가 DB 안에서 일관된 트랜잭션 단위로 관리된다
- `Redis` cache miss 시에도 항상 동일한 DB 결과를 기준으로 응답할 수 있다

주의:

- raw `BYTEA samples`를 PL/pgSQL에서 반복 decode하며 rollup을 만드는 것은 권장하지 않는다
- `signal_frame_features`는 on-demand cache이므로 rollup 생성의 선행조건으로 두지 않는다
- 즉, `rollup aggregation은 DB 내부`, `원시 binary 해석은 별도 단계`로 분리하는 쪽이 안전하다

```mermaid
flowchart TB
    Raw["device_signal_frames"] --> Rollup62["62.5ms / 250ms / 1s"]
    Rollup62 --> Rollup4["4s / 16s / 64s"]
    Rollup4 --> Rollup256["256s / 1024s"]
    Raw --> FeatureStage["signal_frame_features (on-demand)"]
    Job["Timescale job / pg_cron"] --> Rollup62
    Job --> Rollup4
    Job --> Rollup256
```

## 표준 프론트엔드 제안

초기 표준 프론트엔드는 `Vue 3 + Naive UI + ECharts` 조합을 권장한다.

이유:

- 현재 control panel과 기술 스택 정렬이 쉽다
- ECharts는 multi-series, zoom, dataZoom, tooltip이 성숙하다
- component packaging이 쉬워 다른 콘솔에도 이식하기 좋다
- 서버에서 내려준 envelope/rollup series를 그대로 그리기 좋다

### 프론트엔드 역할

- device/stream 선택
- 시간 범위 선택
- viewport width 계산 후 `max_points` 전달
- query-api 응답을 ECharts option으로 변환
- zoom/pan 이벤트를 query-api 재요청으로 연결

### 프론트엔드가 하지 말아야 할 일

- raw frame 전체 decode
- 브라우저 단독 downsampling
- 수만~수십만 점을 무조건 한 번에 렌더링
- DB schema를 직접 아는 쿼리 로직 보유

### 응답 포맷 기준

- 모든 query endpoint는 기본 응답 포맷으로 JSON을 사용
- `GET /series`, `GET /frames`는 큰 payload가 자주 발생하므로 `gzip` 또는 `br` 압축을 기본 적용
- 아주 작은 응답은 압축 이득이 작으므로 threshold 이하에서는 압축을 생략할 수 있다

### 응답 압축 정책

현재 권장안:

- JSON 응답은 `Accept-Encoding` 협상 기반으로 `gzip` 또는 `br` 압축
- 브라우저와 reverse proxy가 기본 지원하는 경로를 우선 사용
- `series`와 `frames` 같은 heavy endpoint에 압축을 우선 적용
- `streams` 목록과 `summary`도 payload가 커지면 동일 정책을 적용

권장 이유:

- query frontend 구현 복잡도가 낮다
- API 디버깅이 쉽다
- 별도 binary serialization layer 없이도 충분한 절감 효과를 얻을 수 있다
- server-side downsampling이 이미 가장 큰 트래픽 절감 요인이므로 추가 binary 포맷의 우선순위가 낮다

주의:

- 압축은 `CPU <-> 네트워크` trade-off가 있으므로 최소 크기 threshold를 둔다
- `Redis`에는 압축 전 JSON을 넣을지, 압축 후 바이트를 넣을지 구현 시 일관되게 정한다
- 내부망 환경이라도 장기적으로 브라우저 렌더링 앞단의 응답 크기를 줄이는 데 의미가 있다

## 표준 컴포넌트 경계

query frontend는 페이지가 아니라 재사용 가능한 컴포넌트로 둔다.

현재 구현:

- 위치: `frontend/stream-viewer`
- package name: `@aetus/stream-viewer`
- framework: `Vue 3`
- UI: `Naive UI`
- chart engine: `ECharts`
- export: `AetusStreamViewer`

기본 props:

| Prop | 설명 |
| --- | --- |
| `queryServerUrl` | query-api base URL |
| `authToken` | query-api용 bearer JWT |
| `tokenProvider` | token 만료/교체 시 host app이 JWT를 공급하는 async callback |
| `deviceId` | 단일 초기 장치 ID. `initialDeviceIds`가 없을 때 호환용으로 사용 |
| `initialDeviceIds` | 초기 장치 ID 목록. 여러 device의 같은 stream key를 overlay 가능 |
| `initialStreamKey` | 초기 stream key |
| `initialRangePreset` | 초기 범위 preset. `10m`, `1h`, `6h`, `1d` |
| `maxPointsPerRequest` | 요청당 차트 해상도 상한. 기본값 `10000` |
| `autoOpenControls` | 최초 렌더링 시 drawer control을 열지 여부 |
| `panelTitle` | 패널 상단 eyebrow 문구 |

사용 예:

```vue
<script setup lang="ts">
import { AetusStreamViewer } from "@aetus/stream-viewer";
import "@aetus/stream-viewer/style.css";
</script>

<template>
  <AetusStreamViewer
    query-server-url="http://127.0.0.1:18001"
    :auth-token="queryJwt"
    :initial-device-ids="['dense-device-1', 'dense-device-2']"
    initial-stream-key="dense.vibration"
    initial-range-preset="10m"
    :max-points-per-request="10000"
  />
</template>
```

현재 컴포넌트 동작:

- 평상시에는 chart surface와 작은 상태 정보만 노출하고, device/stream/channel/range 설정은 우측 drawer에 숨긴다
- `GET /v1/query/devices/{device_id}/streams`로 device별 stream metadata 조회
- 여러 device에서 같은 `stream.key`를 선택하면 같은 chart에 overlay한다
- `GET /v1/query/devices/{device_id}/streams/{key}/series`로 chart series 조회
- numeric `scalar` stream은 단일 line series로 렌더링
- string `scalar` stream은 해당 시점의 세로 점선 marker로 렌더링
- `sampled` stream은 channel별 min/max envelope로 렌더링
- `10m`, `1h`, `6h`, `1d` 범위 preset, custom from/to range, `max_points` 제어 제공
- ECharts zoom 이벤트가 발생하면 현재 visible range를 query-api에 재요청해 high-density 데이터를 가져온다
- query range, stream, device 변경 시 이전 요청은 `AbortController`로 취소한다
- adjacent range prefetch는 opportunistic하게 수행하며 실패해도 현재 chart를 방해하지 않는다

host application 연동 이벤트:

- `range-change`
- `device-change`
- `stream-change`
- `query-start`
- `query-success`
- `query-error`
- `auth-expired`
- `density-change`

현재 e2e 검증:

- query-api base URL을 prop으로만 받아 mocked query-api와 통신
- JWT bearer header 포함 여부
- 2개 device의 같은 sampled stream overlay
- 10분 범위에서 device별 2 channel x 10,000 point 응답 렌더링
- scalar `double`, `float`, `int`, `bool`, `string` value type 전환 렌더링
- zoom/high-density visible range 재요청

## 인증/인가 방향

query-api 인증은 ingest 인증과 분리한다.

중요한 구분:

- ingest 인증: 기기가 데이터를 업로드할 수 있는지 확인
- query 인증: 운영자 또는 서비스가 저장된 데이터를 읽을 수 있는지 확인

따라서 query-api는 device token, bootstrap token, HMAC upload secret을 직접 사용하지 않는다.

```mermaid
flowchart TB
    Operator["Operator / dashboard shell"] --> Issuer["Auth issuer"]
    Issuer --> QueryAuth["POST /v1/auth/token"]
    QueryAuth --> JWT["Short-lived query JWT"]
    JWT --> Viewer["AetusStreamViewer"]
    Viewer --> Query["query-api"]
    Query --> Verify["JWT verify"]
    Verify --> Policy["scope / device / site check"]
    Policy --> PG["PostgreSQL / TimescaleDB"]
```

권장 기본안:

- query-api가 `POST /v1/auth/token`으로 짧은 수명의 query JWT를 발급한다
- token 발급 API는 `X-Aetus-Admin-Token`을 요구한다
- operator UI 또는 host shell은 admin/service credential을 직접 브라우저에 노출하지 않고 backend에서 query JWT를 발급받아 stream-viewer에 전달한다
- query JWT는 짧은 만료 시간을 갖는다
- `/v1/healthz`, `/v1/readyz`는 인증 없이 허용한다
- `/v1/query/*`는 `Authorization: Bearer <jwt>`를 요구한다
- 인증 실패는 `401`, 인증은 되었지만 권한이 없으면 `403`으로 구분한다

이유:

- device credential이 UI에 노출되지 않는다
- 권한 단위를 `읽기 전용`, `특정 device group`, `관리자` 등으로 나누기 쉽다
- query-api를 공개망 또는 사내 SSO 뒤로 옮겨도 ingest plane과 충돌하지 않는다

### JWT claim 모델

초기 claim은 단순하게 유지하되, device/site 단위 제한을 걸 수 있는 구조는 열어둔다.

권장 예:

```json
{
  "iss": "aetus-query-api",
  "sub": "operator-123",
  "aud": "aetus-stream-viewer",
  "iat": 1760000000,
  "exp": 1760003600,
  "scope": ["query:read", "streams:list", "frames:read"],
  "devices": ["dense-device-1", "dense-device-2"],
  "device_groups": ["line-1"],
  "streams": ["dense.vibration", "*"],
  "max_range_seconds": 86400,
  "max_points": 10000
}
```

claim 의미:

| Claim | 설명 |
| --- | --- |
| `iss` | 발급자. query-api 설정과 일치해야 함 |
| `aud` | 대상 audience. 기본값 예: `aetus-query-api` |
| `sub` | 사용자 또는 service principal ID |
| `scope` | 기능 권한. `streams:list`, `query:read`, `frames:read` |
| `devices` | 개별 device allowlist |
| `device_groups` | production line 또는 group 단위 조회 권한. v1은 claim 보존 중심 |
| `streams` | stream key allowlist |
| `max_range_seconds` | token이 허용하는 최대 조회 기간 |
| `max_points` | token이 허용하는 최대 chart point 요청 수 |

와일드카드 정책:

- `devices=["*"]` 또는 `streams=["*"]`는 내부 admin 전용으로만 사용한다
- 일반 operator token은 site/group 중심으로 제한한다
- 개별 `devices` claim은 예외 공유나 디버깅용으로 둔다

### device별 권한에 대한 판단

device별 권한 자체는 구현 난도가 높지 않다.
복잡해지는 부분은 권한을 관리하는 UI, ACL DB, 권한 변경의 즉시 반영, 사용자 그룹 정책이다.

따라서 v1은 다음 절충안을 따른다.

- query-api는 JWT claim 기반 read-time check만 수행한다
- 기본 운영 단위는 `site` 또는 `group`
- `devices` claim은 optional allowlist로 지원한다
- 별도 사용자/역할 관리 UI는 만들지 않는다
- stream allowlist는 `streams` claim으로 지원하되, 별도 ACL DB/UI는 v1 범위에서 제외한다

권한 판정 순서:

```mermaid
flowchart TD
    Req["GET /v1/query/devices/{device_id}/..."] --> Auth{"Valid JWT?"}
    Auth -- "no" --> R401["401 Unauthorized"]
    Auth -- "yes" --> Scope{"scope includes query:read?"}
    Scope -- "no" --> R403["403 Forbidden"]
    Scope -- "yes" --> All{"devices includes *?"}
    All -- "yes" --> Allow["allow"]
    All -- "no" --> Meta["load device metadata"]
    Meta --> Device{"device_id in devices?"}
    Device -- "yes" --> Allow
    Device -- "no" --> R403
```

### 알고리즘과 key 관리

현재 구현은 `HS256` shared secret을 사용한다.
분리망/내부망 기준으로는 배포와 운영이 단순하다는 장점이 있다.
공개망 또는 외부 SSO 연동이 필요해지면 `RS256`/`ES256` + JWKS 검증 경로를 추가한다.

환경변수 예:

```bash
AETUS_QUERY_AUTH_ENABLED=true
AETUS_QUERY_JWT_SECRET=dev-query-secret-with-at-least-32-bytes
AETUS_QUERY_JWT_ISSUER=aetus-query-api
AETUS_QUERY_JWT_AUDIENCE=aetus-stream-viewer
AETUS_QUERY_JWT_TTL_SECONDS=900
AETUS_QUERY_JWT_MAX_TTL_SECONDS=3600
AETUS_QUERY_ADMIN_TOKEN=internal-admin-token
```

### query-api endpoint별 인증 정책

| Endpoint | 인증 | 비고 |
| --- | --- | --- |
| `GET /v1/healthz` | no | k8s liveness |
| `GET /v1/readyz` | no | k8s readiness |
| `POST /v1/auth/token` | admin token | query JWT 발급 |
| `GET /v1/query/devices/{device_id}/streams` | yes | device/stream claim 확인 |
| `GET /v1/query/devices/{device_id}/streams/{key}/series` | yes | device/stream/range/max_points claim 확인 |
| `GET /v1/query/devices/{device_id}/streams/{key}/summary` | yes | device/stream/range claim 확인 |
| `GET /v1/query/devices/{device_id}/streams/{key}/frames` | yes | `frames:read`, device/stream/range claim 확인, raw drilldown window 제한 유지 |

### frontend 연동

`AetusStreamViewer`는 admin token을 다루지 않는다.
이미 발급된 JWT를 `authToken`으로 받거나, host application이 제공하는 `tokenProvider` callback을 호출한다.

권장 역할:

- host application: 로그인, refresh, token 보관, 만료 처리
- host application backend: `POST /v1/auth/token` 호출
- stream-viewer: `authToken` 또는 `tokenProvider` 결과를 받아 `Authorization` header 추가

예:

```vue
<AetusStreamViewer
  query-server-url="https://query.internal"
  :auth-token="queryJwt"
  :initial-device-ids="['esp32c5-test-001']"
/>
```

주의:

- device token은 브라우저에 넘기지 않는다
- query JWT는 read-only scope로 제한한다
- token 만료 시 component는 `auth-expired` event를 발생시킨다

### v1 비범위

- query-api 내부 로그인 화면
- refresh token 발급
- 사용자/역할 관리 UI
- 권한 변경 즉시 revoke
- device token을 query token으로 교환하는 API

## 캐시 전략

초기부터 `Redis`를 도입하는 방향을 기본안으로 둔다.

- `query-api`는 series/summary 결과를 `Redis`에 `TTL cache`한다
- 캐시 키: `device_id + stream_key + kind + from + to + resolution + max_points`
- 요청은 먼저 고정 rollup tier와 bucket boundary로 정규화한 뒤 cache key를 만든다
- 캐시 값은 JSON 직렬화 결과 또는 내부 object 중 하나로 일관되게 선택한다

이 선택이 과하지 않은 이유:

- query-api는 ingest와 분리되어 있어 Redis 의존성이 write path에 번지지 않는다
- 캐시 용도가 단순 `GET/SET + TTL` 중심이면 운영 복잡도 증가가 크지 않다
- replica 수가 늘어나도 cache warm-up이 공유된다

초기 비범위:

- distributed lock
- pub/sub invalidation
- write-through cache

## k8s 배포 단위

```mermaid
flowchart TB
    subgraph NS["namespace: query"]
        API["query-api deployment"]
        HPA["HPA"]
        SVC["ClusterIP service"]
    end

    API --> PG["PostgreSQL / TimescaleDB"]
    API --> Cache["Redis"]
```

초기 권장:

- `query-api`는 ingest와 별 deployment
- CPU request는 ingest보다 높게 잡을 수 있음
- connection pool은 read-only workload에 맞춰 따로 조정

## 권장 구현 순서

완료:

1. `query-api` read-only FastAPI service 생성
2. `GET /streams`, `GET /summary` JSON 엔드포인트 구현
3. `GET /series`, `GET /frames` JSON 엔드포인트 구현
4. `gzip` 응답 압축 적용
5. raw frame 기반의 좁은 구간 조회 구현
6. `Redis` cache 연결
7. `signal_frame_features` on-demand materialization 추가
8. `Vue + Naive UI + ECharts` 표준 viewer 컴포넌트 작성
9. frontend mocked e2e 추가

남은 작업:

1. query-api JWT 인증/인가 구현
2. stream-viewer `authToken` prop과 401/403 error event 추가
3. 고정 `x4` tier rollup 생성 job 구현
4. overview, zoom, drill-down UX 연결
5. feature-based search/filter 추가
6. `br` 압축 또는 reverse proxy 압축 정책 확정

## 고밀도 테스트 데이터 생성

대량 signal query와 frontend 렌더링을 확인하기 위해 `services/query-api/tools/seed_dense_query_data.py`를 둔다.

기본값은 `1시간` 구간에 `1,002,000` sample point를 생성하고, 같은 device에 `double`, `float`, `int`, `bool`, `string` scalar 예제 stream도 함께 생성한다.

```bash
cd services/query-api
uv run python tools/seed_dense_query_data.py \
  --dsn postgresql://aetus:aetus@127.0.0.1:15432/aetus \
  --device-id dense-device-1 \
  --stream-key dense.vibration \
  --points 1002000 \
  --duration-seconds 3600 \
  --start-iso 2026-05-03T00:00:00Z
```

생성 방식:

- `devices`, `device_boot_sessions`, `signal_stream_definitions` dimension row 생성 또는 재사용
- `device_signal_frames`에 `float32_le` / `interleaved` frame block 삽입
- `metric_definitions`와 `device_metric_points`에 `env.temperature(double)`, `env.humidity(float)`, `motor.rpm(int)`, `pump.enabled(bool)`, `machine.state(string)` 삽입
- 기본 `frame_samples=1000`이므로 100만 point는 약 1002개 frame row로 저장
- frontend는 `dense-device-1`과 `dense.vibration`을 지정해 query-api 경유로 조회할 수 있다

## 현재 시점의 권장 결정

현재 바로 확정해도 좋은 항목:

- ingest와 query는 서비스 분리
- signal 차트는 server-side downsampling 전제
- raw frame은 보관하되 기본 차트 소스로 사용하지 않음
- rollup tier는 `1초` 중심 고정 `x4` 배율 ladder로 관리
- query-api는 요청 해상도를 고정 tier로 정규화하고 bucket boundary에 정렬
- rollup aggregation은 DB 내부 job으로 수행
- `signal_frame_features`는 query-triggered on-demand cache로 생성
- `signal_frame_features`는 자체 retention 후 자동 삭제
- query cache는 초기부터 `Redis`를 사용
- 표준 프론트엔드는 `Vue 3 + Naive UI + ECharts`
- heavy series endpoint는 `JSON + gzip/br` 압축을 기본으로 사용
- query frontend는 재사용 가능한 컴포넌트/package 형태로 구성
- query-api 인증은 ingest 인증과 분리된 JWT 기반 read-only authorization으로 구성
- v1 권한 모델은 `scope + site/group + optional device allowlist` claim 기반으로 구성

## 추가 합의가 필요한 항목

아래는 구현 전에 한 번 더 합의하면 좋다.

1. JWT 운영 알고리즘을 초기부터 `RS256/JWKS`로 갈지, `HS256` dev path부터 구현할지
2. device metadata에 `site_code` / `group_key`를 query-api 권한 확인용으로 언제 연결할지
3. rollup 생성 job을 Timescale background job으로 둘지, 별도 worker로 둘지
