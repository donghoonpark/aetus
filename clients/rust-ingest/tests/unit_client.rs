use aetus_ingest_client::generated::ingest_event;
use aetus_ingest_client::generated::metric as metric_pb;
use aetus_ingest_client::generated::telemetry_payload;
use aetus_ingest_client::generated::{
    DeviceStatus, EventType, Severity, SignalSampleEncoding, SignalSampleLayout,
};
use aetus_ingest_client::{
    build_alert_event, build_metric_event, build_signal_frame_event, build_status_event, metric,
    pack_signal_samples_f32, pack_signal_samples_i16, AetusIngestClient, AuthMode, Error,
    SignalChannelSpec, SignalLayout,
};
use httpmock::prelude::*;
use httpmock::HttpMockRequest;
use prost::Message;

const DEVICE_ID: &str = "rust-test-device";
const BOOT_ID: &str = "boot-rust-unit";

#[test]
fn metric_sets_expected_oneof_for_supported_rust_values() {
    let cases = vec![
        (metric("i", -7_i64, "count").unwrap(), "int"),
        (metric("f", 22.25_f64, "celsius").unwrap(), "double"),
        (metric("b", true, "").unwrap(), "bool"),
        (metric("s", "ok", "").unwrap(), "string"),
        (metric("blob", vec![1_u8, 2], "").unwrap(), "bytes"),
    ];

    for (item, expected) in cases {
        let actual = match item.value.unwrap() {
            metric_pb::Value::IntValue(value) => {
                assert_eq!(value, -7);
                "int"
            }
            metric_pb::Value::DoubleValue(value) => {
                assert_eq!(value, 22.25);
                "double"
            }
            metric_pb::Value::BoolValue(value) => {
                assert!(value);
                "bool"
            }
            metric_pb::Value::StringValue(value) => {
                assert_eq!(value, "ok");
                "string"
            }
            metric_pb::Value::BytesValue(value) => {
                assert_eq!(value, vec![1_u8, 2]);
                "bytes"
            }
        };
        assert_eq!(actual, expected);
    }
}

#[test]
fn metric_rejects_missing_key() {
    assert!(matches!(metric("", 1_i64, ""), Err(Error::InvalidInput(_))));
}

#[test]
fn metric_event_sets_telemetry_metric_set_and_metadata() {
    let event = build_metric_event(
        DEVICE_ID,
        3,
        BOOT_ID,
        1_002_003,
        1234,
        Some(1_712_345_678_901_234_567),
        vec![
            metric("temperature", 22.25_f64, "celsius").unwrap(),
            metric("ok", true, "").unwrap(),
        ],
    )
    .unwrap();

    assert_eq!(event.schema_version, 1);
    assert_eq!(event.device_id, DEVICE_ID);
    assert_eq!(event.sequence, 3);
    assert_eq!(event.boot_id, BOOT_ID);
    assert_eq!(event.firmware_version, 1_002_003);
    assert_eq!(event.uptime_ms, 1234);
    assert_eq!(event.timestamp_ns, 1_712_345_678_901_234_567);
    assert_eq!(event.event_type, EventType::Telemetry as i32);

    let ingest_event::Body::Telemetry(telemetry) = event.body.unwrap() else {
        panic!("expected telemetry body");
    };
    let telemetry_payload::Payload::MetricSet(metric_set) = telemetry.payload.unwrap() else {
        panic!("expected metric set payload");
    };
    assert_eq!(metric_set.metrics.len(), 2);
    assert_eq!(metric_set.metrics[0].key, "temperature");
    assert_eq!(metric_set.metrics[0].unit, "celsius");
}

#[test]
fn metric_event_rejects_empty_metric_set() {
    assert!(matches!(
        build_metric_event(DEVICE_ID, 0, BOOT_ID, 0, 0, None, vec![]),
        Err(Error::InvalidInput(_))
    ));
}

#[test]
fn pack_signal_samples_interleaved_float32() {
    let packed = pack_signal_samples_f32(
        &[vec![1.0, 2.0, 3.0], vec![4.0, 5.0, 6.0]],
        SignalLayout::Interleaved,
    )
    .unwrap();

    let expected = [1.0_f32, 2.0, 3.0, 4.0, 5.0, 6.0]
        .into_iter()
        .flat_map(f32::to_le_bytes)
        .collect::<Vec<_>>();
    assert_eq!(packed.bytes, expected);
    assert_eq!(packed.sample_count, 2);
}

#[test]
fn pack_signal_samples_planar_int16() {
    let packed = pack_signal_samples_i16(
        &[vec![1, 10], vec![2, 20], vec![3, 30]],
        SignalLayout::Planar,
    )
    .unwrap();

    let expected = [1_i16, 2, 3, 10, 20, 30]
        .into_iter()
        .flat_map(i16::to_le_bytes)
        .collect::<Vec<_>>();
    assert_eq!(packed.bytes, expected);
    assert_eq!(packed.sample_count, 3);
}

#[test]
fn pack_signal_samples_rejects_ragged_rows() {
    assert!(matches!(
        pack_signal_samples_i16(&[vec![1, 2], vec![3]], SignalLayout::Interleaved),
        Err(Error::InvalidInput(_))
    ));
}

#[test]
fn signal_frame_event_builds_channels_and_packed_samples() {
    let channels = vec![
        SignalChannelSpec::new("x", "g").unwrap().scale(0.1),
        SignalChannelSpec::new("y", "g").unwrap().offset(-1.0),
        SignalChannelSpec::new("z", "").unwrap(),
    ];
    let samples = pack_signal_samples_f32(
        &[vec![1.0, 2.0, 3.0], vec![4.0, 5.0, 6.0]],
        SignalLayout::Interleaved,
    )
    .unwrap();
    let event = build_signal_frame_event(
        DEVICE_ID,
        9,
        BOOT_ID,
        0,
        0,
        Some(1_712_345_679_111_000_000),
        "imu.accel",
        5_000_000,
        channels,
        samples,
    )
    .unwrap();

    let ingest_event::Body::Telemetry(telemetry) = event.body.unwrap() else {
        panic!("expected telemetry");
    };
    let telemetry_payload::Payload::SignalFrame(frame) = telemetry.payload.unwrap() else {
        panic!("expected signal frame");
    };
    assert_eq!(frame.stream_key, "imu.accel");
    assert_eq!(frame.sample_interval_ns, 5_000_000);
    assert_eq!(frame.sample_count, 2);
    assert_eq!(frame.encoding, SignalSampleEncoding::Float32Le as i32);
    assert_eq!(frame.layout, SignalSampleLayout::Interleaved as i32);
    assert_eq!(
        frame
            .channels
            .iter()
            .map(|item| item.key.as_str())
            .collect::<Vec<_>>(),
        vec!["x", "y", "z"]
    );
    assert_eq!(frame.channels[0].scale, Some(0.1));
    assert_eq!(frame.channels[1].offset, Some(-1.0));
    assert_eq!(frame.samples.len(), 24);
}

#[test]
fn status_and_alert_event_builders_set_expected_body() {
    let status = build_status_event(
        DEVICE_ID,
        1,
        BOOT_ID,
        0,
        0,
        None,
        DeviceStatus::Degraded,
        -61,
        123456,
        "ota",
    )
    .unwrap();
    let alert = build_alert_event(
        DEVICE_ID,
        2,
        BOOT_ID,
        0,
        0,
        None,
        "laser_fault",
        Severity::Critical,
        "laser current too high",
    )
    .unwrap();

    assert_eq!(status.event_type, EventType::Status as i32);
    let ingest_event::Body::Status(status_body) = status.body.unwrap() else {
        panic!("expected status");
    };
    assert_eq!(status_body.status, DeviceStatus::Degraded as i32);
    assert_eq!(status_body.rssi, -61);
    assert_eq!(status_body.free_heap, 123456);
    assert_eq!(status_body.reboot_reason, "ota");

    assert_eq!(alert.event_type, EventType::Alert as i32);
    let ingest_event::Body::Alert(alert_body) = alert.body.unwrap() else {
        panic!("expected alert");
    };
    assert_eq!(alert_body.severity, Severity::Critical as i32);
    assert_eq!(alert_body.code, "laser_fault");
}

#[test]
fn client_posts_protobuf_headers_and_increments_sequence_on_success() {
    let server = MockServer::start();
    let mock = server.mock(|when, then| {
        when.method(POST)
            .path("/v1/ingest")
            .header("content-type", "application/x-protobuf")
            .header("x-device-id", DEVICE_ID)
            .header("authorization", "Bearer tok");
        then.status(202)
            .header("content-type", "application/json")
            .body(r#"{"request_id":"req-unit","status":"accepted","device_id":"rust-test-device","sequence":7}"#);
    });

    let mut client =
        AetusIngestClient::with_sequence(server.base_url(), DEVICE_ID, "tok", BOOT_ID, 42, 7)
            .unwrap();
    let response = client
        .send_metrics(
            vec![metric("temperature", 22.25_f64, "celsius").unwrap()],
            None,
        )
        .unwrap();

    mock.assert();
    assert_eq!(response.request_id, "req-unit");
    assert_eq!(response.sequence, 7);
    assert_eq!(client.sequence(), 8);
}

#[test]
fn client_can_sign_uploads_with_hmac_auth_mode() {
    let server = MockServer::start();
    let mock = server.mock(|when, then| {
        when.method(POST)
            .path("/v1/ingest")
            .header("content-type", "application/x-protobuf")
            .header("x-device-id", DEVICE_ID)
            .is_true(|request: &HttpMockRequest| {
                let headers = request.headers();
                headers.get("authorization").is_none()
                    && headers.get("x-aetus-signature").is_some_and(|value| {
                        value
                            .to_str()
                            .is_ok_and(|text| text.starts_with("hmac-sha256-v1=") && text.len() == "hmac-sha256-v1=".len() + 64)
                    })
            });
        then.status(202)
            .header("content-type", "application/json")
            .body(r#"{"request_id":"req-hmac","status":"accepted","device_id":"rust-test-device","sequence":0}"#);
    });

    let mut client = AetusIngestClient::with_sequence_and_auth_mode(
        server.base_url(),
        DEVICE_ID,
        "tok",
        BOOT_ID,
        42,
        0,
        AuthMode::HmacSha256,
    )
    .unwrap();
    let response = client
        .send_metrics(
            vec![metric("temperature", 22.25_f64, "celsius").unwrap()],
            None,
        )
        .unwrap();

    mock.assert();
    assert_eq!(response.request_id, "req-hmac");
    assert_eq!(client.sequence(), 1);
}

#[test]
fn hmac_signature_matches_known_vector() {
    let client = AetusIngestClient::with_sequence_and_auth_mode(
        "http://ingest.local",
        DEVICE_ID,
        "secret",
        BOOT_ID,
        42,
        0,
        AuthMode::HmacSha256,
    )
    .unwrap();

    assert_eq!(
        client.hmac_signature("dev", b"\x01\x02test").unwrap(),
        "hmac-sha256-v1=5f289c9b28519726a0e78e73646e9355d2068ea0c5d696909eb384a97e324e5c"
    );
}

#[test]
fn client_keeps_sequence_when_server_rejects_upload() {
    let server = MockServer::start();
    server.mock(|when, then| {
        when.method(POST).path("/v1/ingest");
        then.status(401).body("bad token");
    });

    let mut client =
        AetusIngestClient::with_sequence(server.base_url(), DEVICE_ID, "tok", BOOT_ID, 42, 7)
            .unwrap();
    let error = client
        .send_metrics(
            vec![metric("temperature", 22.25_f64, "celsius").unwrap()],
            None,
        )
        .unwrap_err();

    assert!(matches!(
        error,
        Error::Rejected {
            status_code: 401,
            ..
        }
    ));
    assert_eq!(client.sequence(), 7);
}

#[test]
fn encoded_body_round_trips_through_protobuf_decoder() {
    let event = build_metric_event(
        DEVICE_ID,
        0,
        BOOT_ID,
        42,
        0,
        Some(1_812_345_678_000_000_000),
        vec![metric("temperature", 22.75_f64, "celsius").unwrap()],
    )
    .unwrap();
    let mut bytes = Vec::new();
    event.encode(&mut bytes).unwrap();
    let decoded = aetus_ingest_client::generated::IngestEvent::decode(bytes.as_slice()).unwrap();

    assert_eq!(decoded.device_id, DEVICE_ID);
    assert_eq!(decoded.boot_id, BOOT_ID);
    assert_eq!(decoded.timestamp_ns, 1_812_345_678_000_000_000);
}
