# Protobuf

## protobuf 메시지 구조

필드 설계 원칙:

- upsert/중복 제거에 필요한 값은 공통 헤더에 둠
- 장치 공통 메타데이터는 작게 유지
- 이벤트 종류별 본문은 `oneof`로 분리
- 반드시 `schema_version`을 포함해 계약 변경을 관리함
- 정확하지 않은 RTC에 의존하지 않도록 절대 timestamp는 필수로 두지 않음
- 필요 시 장치 시각을 보조적으로 담기 위해 `timestamp_ns`를 선택 필드로 둘 수 있음

권장 필드:

| Field | 타입 | 필수 여부 | 설명 |
| --- | --- | --- | --- |
| `schema_version` | `uint32` | 필수 | 이벤트 계약 버전 |
| `device_id` | `string` | 필수 | 장치 고유 ID |
| `sequence` | `uint64` | 필수 | 부팅 세션 내부 이벤트 번호 |
| `event_type` | `enum` | 필수 | `telemetry`, `status`, `alert` |
| `boot_id` | `string` | 필수 | 재부팅 세션 식별자 |
| `firmware_version` | `uint32` | 선택 | packed integer 버전 |
| `uptime_ms` | `uint64` | 선택 | 장치 부팅 이후 경과 시간 |
| `timestamp_ns` | `uint64` | 선택 | 장치 기준 ns 단위 절대시각 |
| `body` | `oneof` | 필수 | 실제 이벤트 본문 |

현재 `TelemetryPayload`는 두 종류의 데이터를 상호 배타적으로 담는다.

- `metrics`: 온도, 배터리, RSSI처럼 낮은 빈도의 sparse scalar telemetry
- `signal_frame`: IMU, 진동, ADC burst처럼 일정 sampling interval을 가진 dense numeric sample block

`signal_frame`은 별도 `event_type`으로 분리하지 않고 `event_type=telemetry`의 하위 payload로 둔다. 이렇게 하면 기존 telemetry 검증, 인증, Kafka raw 적재 흐름을 유지하면서 고주파 샘플만 별도 장기 테이블로 펼칠 수 있다.

현재 구현 기준 핵심 proto 구조:

```proto
message TelemetryPayload {
  oneof payload {
    MetricSet metric_set = 1;
    SignalFrame signal_frame = 2;
  }
}

message MetricSet {
  repeated Metric metrics = 1;
}

message Metric {
  string key = 1;

  oneof value {
    sint64 int_value = 2;
    double double_value = 3;
    bool bool_value = 4;
    string string_value = 5;
    bytes bytes_value = 6;
  }

  string unit = 7;
}

message SignalFrame {
  string stream_key = 1;
  uint64 sample_interval_ns = 2;
  uint32 sample_count = 3;
  SignalSampleEncoding encoding = 4;
  SignalSampleLayout layout = 5;
  repeated SignalChannel channels = 6;
  bytes samples = 7;
}

message SignalChannel {
  string key = 1;
  string unit = 2;
  optional float scale = 3;
  optional float offset = 4;
}
```

`SignalFrame` 필드 의미:

| Field | 타입 | 필수 여부 | 설명 |
| --- | --- | --- | --- |
| `stream_key` | `string` | 필수 | 예: `imu.accel`, `laser.range` |
| `sample_interval_ns` | `uint64` | 필수 | 인접 sample 간격 |
| `sample_count` | `uint32` | 필수 | 채널별 sample 개수 |
| `encoding` | `enum` | 필수 | `float32_le`, `int16_le`, `uint16_le`, `int32_le` |
| `layout` | `enum` | 필수 | `interleaved` 또는 `planar` |
| `channels` | `repeated` | 필수 | sample block의 channel metadata |
| `samples` | `bytes` | 필수 | raw little-endian sample bytes |

검증 규칙:

- `samples` 길이는 `sample_count * channel_count * bytes_per_sample(encoding)`과 정확히 일치해야 한다.
- `timestamp_ns`가 있으면 signal frame의 첫 sample 시각으로 해석한다.
- `uptime_ms`가 있으면 signal frame의 첫 sample uptime 보조값으로 해석한다.
- 한 telemetry event는 `metric_set` 또는 `signal_frame` 중 하나만 가진다.
- 서버는 telemetry payload kind에 따라 metric pipeline 또는 signal frame pipeline 중 하나로 publish한다.

## 중복 방지 키

여기서 말하는 중복 방지 키는 "같은 이벤트가 재전송되어도 서버가 한 번만 반영하게 만드는 구분 기준"이다.

예를 들어 장치가 응답을 못 받아 같은 데이터를 다시 보내더라도, 서버는 이 값을 보고 이미 처리한 이벤트인지 판별할 수 있다.

권장 중복 방지 키:

- `device_id + boot_id + sequence`

주의:

- `timestamp_ns`는 중복 방지 키로 사용하지 않음
- `timestamp_ns`는 관측, 외부 정렬, 후처리 보조값으로만 사용
- `sequence = 0`도 정상 이벤트 번호로 취급

`boot_id`를 항상 쓰는 이유:

- 장치가 재부팅되면 `sequence`가 다시 `0`부터 시작함
- `boot_id`가 없으면 이전 부팅 세션의 같은 `sequence`와 충돌할 수 있음
- `boot_id`를 함께 쓰면 부팅 세션별로 이벤트를 안전하게 구분할 수 있음

`boot_ts`보다 `boot_id`를 우선 추천하는 이유:

- RTC 정확도에 의존하지 않음
- 세션 식별 목적에 더 직접적임
- 서버 파싱 시 절대시각 해석이 필요 없음

## boot_id 생성 규칙

권장 규칙:

- 장치가 부팅할 때마다 새 `boot_id`를 생성
- 같은 부팅 세션 동안에는 동일한 `boot_id`를 유지
- 재부팅되면 반드시 다른 `boot_id`를 생성
- reset reason이 무엇이든 장치가 새 부팅 세션으로 진입하면 새 `boot_id`를 사용

권장 형식:

- 사람이 읽기 쉬운 문자열 또는 UUID 성격의 문자열
- 예: `boot-20260427-01`, `boot-a1b2c3d4`

권장 구현 방향:

- RTC에 의존하지 않음
- 난수 또는 부팅 카운터 기반으로 생성
- 중요한 것은 "시각 표현"보다 "부팅 세션마다 달라지는 값"이라는 점

## sequence 규칙

- `sequence`는 각 부팅 세션에서 `0`부터 시작
- 이벤트 생성 시마다 1씩 증가
- 재전송 시에는 기존 이벤트의 `sequence`를 유지
- 재부팅되면 다시 `0`부터 시작

구현 메모:

- 현재 스키마가 `proto3 uint64`이므로 서버는 `sequence` 누락과 `0`을 구분하지 않는다
- 따라서 계약상 "부팅 직후 첫 이벤트는 `sequence = 0`"을 정상값으로 고정하고, 송신 측이 항상 값을 채운다고 본다

이 규칙을 쓰면 서버는 `device_id + boot_id + sequence` 조합만으로 이벤트를 안정적으로 구분할 수 있다.

## protobuf 의미 예시

예를 들어 텔레메트리 이벤트는 개념적으로 아래 정보를 담는다.

- `schema_version = 1`
- `device_id = "esp32c5-001"`
- `boot_id = "boot-20260427-01"`
- `sequence = 0`
- `event_type = EVENT_TYPE_TELEMETRY`
- `telemetry.metric_set.metrics = [temperature, humidity, battery]`
- 또는 `telemetry.signal_frame = imu.accel 200Hz 1초 frame`

## 서버 내부 이벤트 예시

```json
{
  "request_id": "req-7bdb4f1e",
  "received_at": "2026-04-26T09:00:01Z",
  "source_ip": "masked-or-dropped",
  "tenant_id": "default",
  "schema_version": 1,
  "device_id": "esp32c5-001",
  "boot_id": "boot-20260427-01",
  "sequence": 0,
  "event_type": "telemetry",
  "firmware_version": 1002003,
  "timestamp_ns": 1777242001000000000,
  "payload": {
    "kind": "metric_set",
    "metrics": [
      {
        "key": "temperature",
        "type": "double",
        "value": 21.4,
        "unit": "celsius"
      }
    ]
  }
}
```
