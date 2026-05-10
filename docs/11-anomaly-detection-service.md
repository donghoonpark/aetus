# Anomaly Detection Service Plan

## 목적

이 문서는 AETUS에 window 기반 이상감지와 webhook 알림 기능을 추가하기 위한 설계와 현재 PoC 구현 상태를 정리한다.

핵심 목표:

- ingest 경로의 안정성을 해치지 않는다
- PostgreSQL/TimescaleDB에 적재된 metric/signal stream을 기준으로 window 감지를 수행한다
- 감지 결과를 DB에 보존하고 query/frontend에서 overlay로 소비할 수 있게 한다
- 외부 시스템 연동은 webhook outbox로 비동기 처리한다
- 초기에는 rule-based detector를 구현하고, 이후 ML detector를 같은 실행 모델에 붙일 수 있게 한다

## 결론

anomaly 기능은 `services/anomaly`라는 하나의 Rust 서비스 코드베이스로 둔다.

단, 런타임 역할은 분리한다.

- `aetus-anomaly api`: panel/API용 설정 및 조회
- `aetus-anomaly worker`: window 기반 detector 실행
- `aetus-anomaly dispatcher`: webhook outbox 발송/retry/dead-letter 처리

초기 운영은 하나의 Kubernetes Pod 안에서 여러 container로 구성한다.
각 container는 같은 Docker image와 같은 Rust binary를 사용하고 command만 다르게 실행한다.

```mermaid
flowchart TB
    subgraph Pod["anomaly pod"]
        API["aetus-anomaly api"]
        Worker["aetus-anomaly worker"]
        Dispatcher["aetus-anomaly dispatcher"]
    end

    API --> DB["PostgreSQL / TimescaleDB"]
    Worker --> DB
    Dispatcher --> DB
    Dispatcher --> Target["Webhook targets"]

    Query["query-api"] --> DB
    Panel["anomaly-panel"] --> API
    Stream["stream-viewer"] --> Query
```

정합성 있는 상태는 DB를 source of truth로 둔다.
Pod 내부 IPC는 필수로 설계하지 않는다.
worker 상태 표시가 필요하면 DB heartbeat row와 Prometheus metrics를 우선 사용하고, local IPC는 편의 기능으로만 둔다.

현재 PoC 구현 범위:

- `services/anomaly`: Rust `aetus-anomaly api|worker|dispatcher|run-once` binary
- `services/postgres/initdb/20-anomaly.sql`: anomaly job/event/score/webhook schema
- `compose/e2e-compose.yml`: anomaly API, worker, dispatcher container 추가
- `frontend/anomaly-panel`: portable Vue/Naive UI control panel
- `clients/python-ingest/tests/e2e/test_pipeline.py`: 실제 Python ingest client가 데이터를 보낸 뒤 anomaly job을 생성하고 event 생성을 검증
- `.github/workflows/ci.yml`: anomaly service unit test와 anomaly panel build/e2e 추가
- `.github/workflows/container-images.yml`: `aetus-anomaly` GHCR image build/publish 대상 추가

PoC의 detector는 scalar metric과 decoded signal-frame channel window를 모두 처리한다. 현재는 raw signal frame sample을 window 내에서 직접 로드하되 `detector_config.max_points`로 channel별 최대 포인트 수를 제한한다. worker lease, event resolve/ack API, Prometheus metrics, signal feature cache 재사용은 후속 확장 영역이다.

## 왜 DB 뒷단인가

window 기반 이상감지는 과거 구간 조회, late-arrival 처리, 재처리, detector version 변경, backfill이 중요하다.

따라서 Kafka streaming consumer보다 DB 뒷단 worker가 초기 구현에 더 적합하다.

| 방식 | 장점 | 단점 | 판단 |
| --- | --- | --- | --- |
| ingest inline | 즉시 처리 가능 | 수집 지연/장애 영향 큼 | 사용하지 않음 |
| Kafka streaming detector | 저지연 | late arrival, backfill, 모델 변경 복잡 | 추후 low-latency 옵션 |
| DB-backed worker | window 조회, 재처리, 운영 단순 | 초저지연은 아님 | 기본안 |

## 상위 데이터 흐름

```mermaid
flowchart TB
    Device["Device / client"] --> Ingest["ingest-api"]
    Ingest --> Kafka["Kafka"]
    Kafka --> Connect["Kafka Connect"]
    Connect --> Storage["PostgreSQL / TimescaleDB"]

    Storage --> Worker["aetus-anomaly worker"]
    Worker --> Events["anomaly_events"]
    Worker --> Scores["anomaly_scores"]
    Worker --> Outbox["webhook_outbox"]

    Outbox --> Dispatcher["aetus-anomaly dispatcher"]
    Dispatcher --> External["Slack / MES / ERP / custom API"]

    API["aetus-anomaly api"] --> Events
    API --> Jobs["anomaly_jobs"]
    API --> Webhooks["webhook_endpoints"]
    Panel["anomaly-panel"] --> API
    Query["query-api"] --> Events
    Viewer["stream-viewer"] --> Query
```

## 서비스 구조

현재 구현 디렉터리:

```text
services/
  anomaly/
    Dockerfile
    Cargo.toml
    README.md
    src/
      main.rs
      config.rs
      api.rs
      detectors/
        mod.rs
        threshold.rs
      dispatcher.rs
      models.rs
      repository.rs
      webhook.rs
      worker.rs
frontend/
  anomaly-panel/
```

Rust stack:

- `axum`: HTTP API server
- `tokio`: async runtime
- `sqlx`: PostgreSQL access and migrations
- `serde` / `serde_json`: JSONB config, API payload, webhook payload
- `utoipa` / `utoipa-swagger-ui`: OpenAPI schema 준비용 의존성
- `tower-http`: CORS, tracing, compression, request middleware
- `reqwest`: webhook delivery
- `tracing`: structured logs
- `metrics` or `prometheus-client`: Prometheus metrics
- `clap`: multi-command binary

전체 Rust로 가는 이유:

- worker의 signal frame decode, window scan, RMS/stddev/peak 계산이 CPU와 메모리 효율에 민감하다
- API, worker, dispatcher가 같은 DB model과 config type을 공유할 수 있다
- 단일 binary를 `api`, `worker`, `dispatcher` subcommand로 실행해 배포 artifact가 단순해진다
- webhook dispatcher도 async HTTP/retry/timeout 처리에 Rust와 Tokio가 잘 맞는다
- 장기적으로 ML/rule detector를 trait 기반으로 확장하기 쉽다

FastAPI 스타일의 OpenAPI/validation 편의성은 `utoipa`, `serde`, request type validation helper로 보완한다.

## Pod 구성

초기 Kubernetes 구성:

```text
Deployment: anomaly
  Pod:
    container: anomaly-api
      command: aetus-anomaly api
    container: anomaly-worker
      command: aetus-anomaly worker
    container: webhook-dispatcher
      command: aetus-anomaly dispatcher
```

공통 env:

- `AETUS_POSTGRES_DSN`
- `AETUS_ANOMALY_POLL_INTERVAL_SECONDS`
- `AETUS_ANOMALY_WORKER_ID`
- `AETUS_ANOMALY_WEBHOOK_TIMEOUT_SECONDS`
- `AETUS_ANOMALY_WEBHOOK_MAX_ATTEMPTS`
- `AETUS_ANOMALY_WEBHOOK_SIGNING_ENABLED`

스케일 아웃 기준:

- detector job 수가 증가하면 `aetus-anomaly worker` container만 별도 Deployment로 분리
- webhook backlog가 증가하면 `aetus-anomaly dispatcher` container만 별도 Deployment로 분리
- API 부하가 증가하면 `aetus-anomaly api` container만 별도 Deployment로 분리

초기에는 하나의 Pod로 묶어 운영 단순성을 우선한다.

## DB Schema

현재 PoC schema는 normalized telemetry table을 읽되 anomaly 결과 table은 `device_id`, `stream_key`, `channel_key` 텍스트 값을 직접 보관한다. 이는 운영자 조회와 webhook payload 생성이 단순하고, 감지 결과 row 수가 원본 telemetry보다 훨씬 적다는 판단 때문이다. 장기적으로 고카디널리티/대규모 score 보관이 필요해지면 `device_pk`, `metric_pk`, `signal_pk` FK 기반으로 전환할 수 있다.

### anomaly_jobs

감지 작업 정의.

```sql
CREATE TABLE anomaly_jobs (
    job_id BIGSERIAL PRIMARY KEY,
    job_key TEXT NOT NULL UNIQUE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    device_selector JSONB NOT NULL,
    stream_selector JSONB NOT NULL,
    detector_type TEXT NOT NULL,
    detector_config JSONB NOT NULL,
    window_seconds INTEGER NOT NULL,
    step_seconds INTEGER NOT NULL,
    lookback_seconds INTEGER NOT NULL DEFAULT 0,
    severity TEXT NOT NULL DEFAULT 'warning',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

selector 예:

```json
{
  "devices": ["esp32c5-test-001"],
  "streams": ["motor.vibration"],
  "channels": ["accel_x", "accel_y"]
}
```

### anomaly_job_state

worker 진행 상태.

```sql
CREATE TABLE anomaly_job_state (
    job_id BIGINT PRIMARY KEY REFERENCES anomaly_jobs(job_id),
    last_window_end TIMESTAMPTZ NULL,
    lease_owner TEXT NULL,
    lease_until TIMESTAMPTZ NULL,
    heartbeat_at TIMESTAMPTZ NULL,
    last_error TEXT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

목적:

- 같은 job을 여러 worker가 동시에 처리하지 않도록 lease 사용
- API가 worker 상태를 DB에서 조회 가능
- worker 재시작 후 이어서 처리 가능

### anomaly_scores

window별 score.

```sql
CREATE TABLE anomaly_scores (
    score_id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES anomaly_jobs(job_id),
    device_id TEXT NOT NULL,
    stream_key TEXT NOT NULL,
    channel_key TEXT NULL,
    channel_key_norm TEXT NOT NULL DEFAULT '',
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    threshold DOUBLE PRECISION NULL,
    severity TEXT NOT NULL,
    detector_type TEXT NOT NULL,
    detector_version TEXT NOT NULL,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uq_anomaly_scores_window
    ON anomaly_scores(job_id, device_id, stream_key, channel_key_norm, window_start, window_end);
```

주의:

- `channel_key_norm`은 nullable `channel_key`를 안정적으로 unique key에 포함하기 위한 normalizing column이다.
- 현재 PoC는 metric threshold detector를 먼저 구현했지만 schema는 stream/channel 단위 score를 보관할 수 있게 열려 있다.

### anomaly_events

운영자가 보는 이벤트.

```sql
CREATE TABLE anomaly_events (
    event_id UUID PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES anomaly_jobs(job_id),
    device_id TEXT NOT NULL,
    stream_key TEXT NOT NULL,
    channel_key TEXT NULL,
    channel_key_norm TEXT NOT NULL DEFAULT '',
    event_start TIMESTAMPTZ NOT NULL,
    event_end TIMESTAMPTZ NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    score DOUBLE PRECISION NOT NULL,
    threshold DOUBLE PRECISION NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uq_anomaly_events_start
    ON anomaly_events(job_id, device_id, stream_key, channel_key_norm, event_start);
```

event merge 정책:

- 같은 job/device/stream/channel에서 인접 window가 계속 threshold를 넘으면 기존 open event를 확장
- 정상 window가 일정 횟수 이상 이어지면 event를 `resolved`로 전환
- operator가 수동으로 `acknowledged`, `muted`, `closed` 상태로 바꿀 수 있게 확장 가능

### webhook_endpoints

외부 연동 endpoint 정의.

```sql
CREATE TABLE webhook_endpoints (
    endpoint_id BIGSERIAL PRIMARY KEY,
    endpoint_key TEXT NOT NULL UNIQUE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    url TEXT NOT NULL,
    secret TEXT NOT NULL,
    event_filter JSONB NOT NULL DEFAULT '{}'::jsonb,
    max_attempts INTEGER NOT NULL DEFAULT 8,
    timeout_seconds DOUBLE PRECISION NOT NULL DEFAULT 5.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### webhook_outbox

발송 예정/실패/완료 상태.

```sql
CREATE TABLE webhook_outbox (
    outbox_id BIGSERIAL PRIMARY KEY,
    endpoint_id BIGINT NOT NULL REFERENCES webhook_endpoints(endpoint_id),
    event_id UUID NOT NULL REFERENCES anomaly_events(event_id),
    payload_json JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_attempt_at TIMESTAMPTZ NULL,
    last_error TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (endpoint_id, event_id)
);
```

### webhook_deliveries

발송 시도 로그.

```sql
CREATE TABLE webhook_deliveries (
    delivery_id BIGSERIAL PRIMARY KEY,
    outbox_id BIGINT NOT NULL REFERENCES webhook_outbox(outbox_id),
    endpoint_id BIGINT NOT NULL REFERENCES webhook_endpoints(endpoint_id),
    event_id UUID NOT NULL REFERENCES anomaly_events(event_id),
    attempt_number INTEGER NOT NULL,
    status_code INTEGER NULL,
    success BOOLEAN NOT NULL,
    error TEXT NULL,
    duration_ms INTEGER NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## Detector v1

초기 detector는 rule-based로 시작한다.

구현된 detector와 대표 use-case:

| Detector | Score | 주요 설정 | 적합한 use-case |
| --- | --- | --- | --- |
| `threshold` | max 또는 min value | `threshold`, `operator` | 온도, 전압, 압력, 전류 같은 단일 값 상/하한 감지 |
| `range` | 허용 범위 초과 거리 | `min`, `max` | 센서가 정상 운전 범위를 벗어나는지 감지 |
| `mean_threshold` | window 평균 | `threshold`, `operator` | 순간 spike보다 평균 부하/온도/전류가 높은지 감지 |
| `rms_threshold` | RMS | `threshold`, `operator` | 진동/가속도/전류 waveform의 에너지 증가 감지 |
| `peak_abs_threshold` | max absolute value | `threshold`, `operator` | 충격, 과도 진동, 급격한 spike 감지 |
| `stddev_threshold` | 표준편차 | `threshold`, `operator` | 값의 불안정성, 노이즈 증가, 공정 흔들림 감지 |
| `delta_threshold` | window 첫 값과 마지막 값의 절대 차이 | `threshold`, `operator` | 짧은 구간에서 절대 변화량이 큰지 감지 |
| `rate_of_change` | 초당 변화량 | `threshold`, `operator` | sampling interval 차이를 보정한 급상승/급하락 감지 |
| `zscore_threshold` | baseline 대비 sigma 거리 | `baseline`, `tolerance`, `threshold` | 장비별 정상 기준이 다른 값의 outlier 감지 |
| `ewma_deviation` | EWMA 대비 최대 편차 | `baseline`, `alpha`, `threshold` | 천천히 변하는 drift와 갑작스러운 이탈 감지 |
| `missing_data` | sample count | `min_count` | 특정 stream이 window 안에 들어오지 않는 상태 감지 |
| `flatline` | max-min range | `threshold`, `min_count` | 센서값이 거의 움직이지 않는 고착/단선 의심 상태 감지 |
| `stuck_at` | 특정 값 근처 sample count | `expected_value`, `tolerance`, `min_count` | ADC max/min 포화, 고정 오류 코드, 릴레이 상태 고착 감지 |
| `duty_cycle` | ON sample 비율 | `baseline`, `threshold`, `operator` | 모터/펌프/밸브 ON 비율이 비정상적으로 높거나 낮은지 감지 |
| `event_sequence` | event count | `min_count`, `max_count` | anchor window 안에서 필수 event 누락 또는 과도 발생 감지 |
| `fft_threshold` | target 또는 dominant frequency magnitude | `threshold`, `target_frequency_hz`, `fft_sample_limit` | 회전 장비, 모터, 팬, 펌프의 특정 진동 주파수 대역 에너지 증가 감지 |

`operator`는 기본 `gt`이며 `gte`, `lt`, `lte`를 지원한다.

FFT detector는 PoC 단계에서 외부 FFT crate 없이 naive DFT를 사용한다. `target_frequency_hz`가 있으면 해당 bin magnitude를 score로 쓰고, 없으면 DC를 제외한 dominant bin magnitude를 score로 쓴다. 큰 window는 `fft_sample_limit`까지 균등 샘플링한다. 운영 규모에서 frequency detector를 적극 사용하려면 이후 `rustfft` 기반 구현, window function, band energy, feature cache를 추가하는 것이 좋다.

Detector interface:

```rust
pub trait Detector: Send + Sync {
    fn detector_type(&self) -> &'static str;
    fn detector_version(&self) -> &'static str;
    fn evaluate(&self, window: &WindowData, config: &DetectorConfig) -> anyhow::Result<DetectionResult>;
}
```

`WindowData`는 query-api 응답 JSON을 그대로 재사용하지 않는다.
anomaly service 내부 repository가 DB에서 읽은 값을 detector 친화적인 object로 변환한다.

## Window 처리 방식

### metric stream

`device_metric_points`에서 window 범위를 조회한다.

권장 집계:

- count
- min
- max
- avg
- last value
- first value
- stddev

### sampled stream

현재 PoC:

1. `device_signal_frames.samples`를 query-api와 동일한 encoding/layout 규칙으로 decode
2. channel별 `NumericWindow`로 변환
3. `stream_selector.channels`가 있으면 해당 channel만 처리
4. `detector_config.max_points`를 초과하면 channel window를 truncate하고 event details에 `truncated=true` 기록

지원 encoding/layout:

- `float32_le`
- `int16_le`
- `uint16_le`
- `int32_le`
- `interleaved`
- `planar`

후속 최적화 우선순위:

1. `signal_frame_features`에 필요한 window/channel feature가 있으면 사용
2. 없으면 `device_signal_frames.samples`를 query-api와 동일한 decoder로 decode
3. 계산한 feature를 `signal_frame_features`에 upsert
4. 필요하면 `signal_rollup_points`를 보조 입력으로 사용

이 구조는 query-api와 비슷하지만, anomaly service가 query-api HTTP를 호출하지 않고 DB repository를 직접 사용한다.

이유:

- batch/window 처리에서 HTTP overhead를 피함
- detector가 feature cache를 직접 생성/재사용 가능
- query-api 장애가 감지 worker에 전파되지 않음

### event-anchored window

일반 window는 latest timestamp 기준으로 `window_seconds`만큼 과거를 읽는다.
event-anchored window는 별도 anchor stream의 event timestamp를 기준으로 target stream의 window를 잡는다.

예:

- `machine_state == "RUN"` 이후 10초 동안 `motor.vibration` RMS 검사
- `door_open == true` 전후 3초 동안 IMU peak 검사
- `temperature` metric 수신 후 5초 동안 signal frame peak 검사

설정 예:

```json
{
  "detector_type": "rms_threshold",
  "stream_selector": {
    "streams": ["motor.vibration"],
    "channels": ["accel_x"]
  },
  "detector_config": {
    "threshold": 0.5,
    "max_points": 10000,
    "anchor": {
      "stream": "machine_state",
      "value_string": "RUN",
      "pre_seconds": 0,
      "post_seconds": 10,
      "max_events": 1
    }
  }
}
```

anchor filter는 `value_string`, `value_bool`, `value_int`, `value_double`를 지원한다. filter를 생략하면 해당 anchor stream의 최신 event timestamp를 사용한다.

### config 필드 요약

| Field | 사용 detector | 의미 |
| --- | --- | --- |
| `threshold` | 대부분의 threshold 계열, `flatline`, `fft_threshold` | crossing 기준값 |
| `operator` | threshold 계열 | `gt`, `gte`, `lt`, `lte` |
| `min`, `max` | `range` | 허용 정상 범위 |
| `baseline` | `zscore_threshold`, `ewma_deviation`, `duty_cycle` | 기준값 또는 ON 판정 기준 |
| `tolerance` | `zscore_threshold`, `stuck_at` | sigma 또는 허용 오차 |
| `alpha` | `ewma_deviation` | EWMA smoothing factor |
| `expected_value` | `stuck_at` | 고착 여부를 볼 특정 값 |
| `min_count`, `max_count` | `missing_data`, `flatline`, `stuck_at`, `event_sequence` | 필요한 최소/최대 sample 또는 event 개수 |
| `target_frequency_hz` | `fft_threshold` | 검사할 목표 주파수 |
| `fft_sample_limit` | `fft_threshold` | DFT에 사용할 최대 sample 수 |
| `max_points` | 모든 detector loader | window/channel별 로드할 최대 point 수 |
| `anchor` | 모든 detector | event 기반 window anchor |

## Worker 알고리즘

```mermaid
flowchart TB
    Tick["poll interval"] --> Jobs["load enabled jobs"]
    Jobs --> Lease["acquire job lease"]
    Lease --> Windows["compute due windows"]
    Windows --> Load["load metric/signal window data"]
    Load --> Detect["run detector"]
    Detect --> Score["upsert anomaly_scores"]
    Score --> Event{"threshold crossed?"}
    Event -- "yes" --> UpsertEvent["merge/upsert anomaly_events"]
    Event -- "yes" --> Outbox["enqueue webhook_outbox"]
    Event -- "no" --> Resolve["maybe resolve open event"]
    Outbox --> State["update job_state"]
    Resolve --> State
```

Lease 원칙:

- `SELECT ... FOR UPDATE SKIP LOCKED` 또는 conditional update로 job lease 획득
- `lease_until`이 지난 job은 다른 worker가 가져갈 수 있음
- worker는 window 처리 중 heartbeat 갱신

Window 원칙:

- `window_seconds`: 판단 구간
- `step_seconds`: 다음 window 이동 폭
- `lookback_seconds`: late arrival 보정을 위해 마지막 처리 지점을 약간 되돌림
- 같은 score/event는 unique key로 idempotent upsert

## Webhook 설계

Detector는 외부 HTTP를 직접 호출하지 않는다.

```mermaid
sequenceDiagram
    participant Worker as aetus-anomaly worker
    participant DB as PostgreSQL
    participant Dispatcher as aetus-anomaly dispatcher
    participant Target as Webhook target

    Worker->>DB: "anomaly_events upsert"
    Worker->>DB: "webhook_outbox insert"
    Dispatcher->>DB: "claim pending outbox"
    Dispatcher->>Target: "POST signed payload"
    Target-->>Dispatcher: "2xx / error"
    Dispatcher->>DB: "delivery log + status update"
```

### Payload

```json
{
  "event_id": "019...",
  "job_key": "motor-vibration-rms",
  "device_id": "device-001",
  "stream_key": "motor.vibration",
  "channel_key": "accel_x",
  "severity": "warning",
  "status": "open",
  "detector": {
    "type": "rms_threshold",
    "version": "1.0.0"
  },
  "window": {
    "from": "2026-05-10T12:00:00Z",
    "to": "2026-05-10T12:00:10Z"
  },
  "score": 0.93,
  "threshold": 0.8,
  "summary": "RMS exceeded threshold"
}
```

### Signature

Webhook request headers:

- `X-Aetus-Webhook-Id`
- `X-Aetus-Webhook-Timestamp`
- `X-Aetus-Webhook-Signature`

Signing input:

```text
<timestamp>.<raw_json_body>
```

Signature:

```text
hmac-sha256=<hex(HMAC_SHA256(endpoint_secret, signing_input))>
```

Retry:

- exponential backoff + jitter
- 2xx success
- 4xx는 설정에 따라 immediate dead-letter 또는 limited retry
- 5xx/network timeout은 retry
- max attempts 초과 시 `dead_letter`

## API v1 초안

Base path: `/v1/anomaly`

| Endpoint | Method | 설명 |
| --- | --- | --- |
| `/v1/healthz` | GET | liveness, 구현됨 |
| `/v1/readyz` | GET | DB 연결 확인, 구현됨 |
| `/jobs` | GET | detector job 목록, 구현됨 |
| `/jobs` | POST | detector job 생성/upsert, 구현됨 |
| `/jobs/{job_id}/run` | POST | 특정 job 수동 1회 실행, 구현됨 |
| `/events` | GET | anomaly event 최근 목록, 구현됨 |
| `/webhooks/endpoints` | GET | webhook endpoint 목록, 구현됨 |
| `/webhooks/endpoints` | POST | endpoint 생성/upsert, 구현됨 |
| `/jobs/{job_id}` | GET | detector job 상세, 미구현 |
| `/jobs/{job_id}` | PATCH | enable/disable, config 수정, 미구현 |
| `/events/{event_id}` | GET | event 상세, 미구현 |
| `/events/{event_id}` | PATCH | ack/close/mute 상태 변경, 미구현 |
| `/webhooks/endpoints/{endpoint_id}` | PATCH | enable/disable/config 수정, 미구현 |
| `/webhooks/deliveries` | GET | delivery 로그 조회, 미구현 |
| `/webhooks/outbox/{outbox_id}/retry` | POST | dead-letter/pending 재시도, 미구현 |

인증:

- 현재 구현은 `x-aetus-admin-token` header 기반 internal admin token으로 시작한다.
- 장기적으로는 host application이 operator auth를 담당하고 anomaly API에는 internal token/JWT만 전달한다.
- device token, bootstrap token, ingest HMAC secret은 anomaly frontend/API에 노출하지 않는다.

## Frontend: anomaly-panel

위치:

```text
frontend/anomaly-panel/
```

형태:

- Vue 3 portable component
- Naive UI
- host app이 `anomalyServerUrl`, `queryServerUrl`, `authToken` 또는 `tokenProvider`를 전달
- `stream-viewer`와 직접 결합하지 않고 drill-down link/event로 연동

초기 화면:

- detector job list
- recent anomaly event list
- event severity/status filter
- selected event detail
- webhook endpoint status
- webhook delivery/dead-letter list
- worker heartbeat / backlog / last run summary

Stream drill-down:

- anomaly event 클릭 시 `device_id`, `stream_key`, `event_start`, `event_end`를 host app에 emit
- host app은 기존 `AetusStreamViewer`에 range를 넘겨 시각화

## 구현 단계

### Phase 0: DB migration

- `services/postgres/initdb/20-anomaly.sql` 추가
- anomaly table 생성
- 주요 index 추가
- expired feature cleanup 또는 anomaly retention 정책 추가

검증:

- plain PostgreSQL에서 schema 생성 가능
- TimescaleDB 이미지에서도 schema 생성 가능

### Phase 1: anomaly service skeleton

- `services/anomaly` Rust crate 추가
- `aetus-anomaly api|worker|dispatcher` subcommand 추가
- `axum` 기반 health/ready endpoint
- config/repository/error/metrics skeleton 추가
- `utoipa` OpenAPI skeleton 추가
- Dockerfile 추가
- compose에 `anomaly-api`, `anomaly-worker`, `webhook-dispatcher` 추가
- GHCR workflow matrix에 `anomaly` image 추가

검증:

- `cargo fmt --check`
- `cargo clippy`
- `cargo test`
- compose health check
- image build

### Phase 2: detector worker v1

- job lease/state 구현
- metric/signal numeric window loader 구현됨
- threshold/range/mean/RMS/peak/stddev/delta/missing-data/flatline detector 구현됨
- event-anchored window 구현됨
- score/event upsert
- event merge/resolve 기본 정책

검증:

- seeded metric/signal data로 worker 실행
- `anomaly_scores`, `anomaly_events` row 검증
- idempotent rerun 검증
- late-arrival lookback 검증

### Phase 3: webhook outbox

- endpoint/outbox/delivery repository
- HMAC signed POST
- retry/backoff/dead-letter
- fake webhook server E2E

검증:

- 2xx success
- 5xx retry
- timeout retry
- max attempts dead-letter
- signature header 검증

### Phase 4: anomaly-panel

- portable Vue component
- event/job/webhook views
- drill-down event emit
- Playwright mocked API tests

검증:

- event list/status filter
- webhook delivery state rendering
- stream-viewer drill-down event contract

### Phase 5: integration E2E

- dense sample data 생성
- anomaly job 생성
- worker run
- event 생성
- webhook delivery
- query/frontend overlay 소비

검증:

- Docker Compose E2E
- GitHub CI default에는 unit + lightweight integration
- heavy dense E2E는 별도 workflow 또는 opt-in marker

## 테스트 전략

Unit:

- detector math
- window planner
- event merge/resolve policy
- webhook signing
- retry schedule

Integration:

- repository CRUD
- lease acquisition
- score/event upsert idempotency
- signal frame decode integration

E2E:

- metric threshold event
- signal frame event
- string telemetry anchored signal-frame event
- webhook delivery success/retry/dead-letter
- anomaly API event query
- anomaly-panel rendering

운영 시나리오:

- worker restart 후 last window 이어서 처리
- 같은 job을 두 worker가 동시에 잡지 않음
- DB 장애 시 API readyz 실패
- webhook target 장애가 detection을 막지 않음

## Retention

권장 기본값:

- `anomaly_scores`: 90일
- `anomaly_events`: 1년 또는 운영 정책
- `webhook_deliveries`: 30일
- `webhook_outbox`: delivered row 30일, dead-letter row 90일

TimescaleDB 사용 시 `anomaly_scores`는 hypertable 후보다.
plain PostgreSQL에서는 k8s CronJob 또는 `aetus-anomaly worker` periodic cleanup으로 삭제한다.

## 운영 Metrics

Prometheus 후보:

- `aetus_anomaly_worker_heartbeat_timestamp`
- `aetus_anomaly_jobs_enabled_total`
- `aetus_anomaly_windows_processed_total`
- `aetus_anomaly_events_created_total`
- `aetus_anomaly_detector_failures_total`
- `aetus_anomaly_webhook_pending_total`
- `aetus_anomaly_webhook_deliveries_total`
- `aetus_anomaly_webhook_failures_total`
- `aetus_anomaly_webhook_dead_letters_total`

## 설계 원칙

- ingest 경로에는 이상감지 로직을 넣지 않는다.
- query-api에 detector 실행 책임을 넣지 않는다.
- anomaly service는 DB를 source of truth로 사용한다.
- webhook은 outbox/dispatcher로 분리한다.
- 초기 detector는 rule-based로 시작한다.
- ML detector는 같은 job/window/event 모델 위에 추가한다.
- 프론트엔드는 portable panel로 제공하고 host app이 stream-viewer와 조합한다.
