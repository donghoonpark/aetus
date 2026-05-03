use aetus_ingest_client::generated::DeviceStatus;
use aetus_ingest_client::{
    metric, pack_signal_samples_f32, AetusIngestClient, SignalChannelSpec, SignalLayout,
};
use postgres::{Client, NoTls};
use reqwest::blocking;
use serde_json::json;
use std::process::Command;
use std::thread::sleep;
use std::time::{Duration, Instant};

const INGEST_API_URL: &str = "http://127.0.0.1:18000";
const KAFKA_CONNECT_URL: &str = "http://127.0.0.1:18083";
const POSTGRES_DSN: &str = "postgresql://aetus:aetus@127.0.0.1:15432/aetus";

fn repo_root() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf()
}

fn docker_compose(args: &[&str]) {
    let compose_file = repo_root().join("compose/e2e-compose.yml");
    let status = Command::new("docker")
        .args(["compose", "-f"])
        .arg(compose_file)
        .args(args)
        .status()
        .expect("failed to run docker compose");
    assert!(status.success(), "docker compose command failed: {args:?}");
}

fn wait_for_http(url: &str) {
    let deadline = Instant::now() + Duration::from_secs(120);
    while Instant::now() < deadline {
        if let Ok(response) = blocking::get(url) {
            if response.status().is_success() {
                return;
            }
        }
        sleep(Duration::from_secs(2));
    }
    panic!("timed out waiting for {url}");
}

fn wait_for_raw_rows(
    device_id: &str,
    expected_count: i64,
) -> Vec<(String, String, i64, String, i64, String)> {
    let deadline = Instant::now() + Duration::from_secs(120);
    while Instant::now() < deadline {
        let mut db = Client::connect(POSTGRES_DSN, NoTls).unwrap();
        let rows = db
            .query(
                r#"
                SELECT device_id, boot_id, sequence, event_type, timestamp_ns, payload_json
                FROM raw_device_events
                WHERE device_id = $1
                ORDER BY sequence ASC
                "#,
                &[&device_id],
            )
            .unwrap();
        if rows.len() as i64 >= expected_count {
            return rows
                .into_iter()
                .map(|row| {
                    (
                        row.get(0),
                        row.get(1),
                        row.get::<_, i64>(2),
                        row.get(3),
                        row.get::<_, Option<i64>>(4).unwrap_or_default(),
                        row.get(5),
                    )
                })
                .collect();
        }
        sleep(Duration::from_secs(2));
    }
    panic!("timed out waiting for raw rows for {device_id}");
}

#[allow(clippy::type_complexity)]
fn wait_for_metric_rows(
    device_id: &str,
    expected_count: i64,
) -> Vec<(
    String,
    String,
    i64,
    i32,
    String,
    String,
    String,
    Option<f64>,
    Option<i64>,
    Option<bool>,
    i64,
)> {
    let deadline = Instant::now() + Duration::from_secs(120);
    while Instant::now() < deadline {
        let mut db = Client::connect(POSTGRES_DSN, NoTls).unwrap();
        let rows = db
            .query(
                r#"
                SELECT
                    d.device_id,
                    b.boot_id,
                    p.sequence,
                    p.metric_index,
                    md.metric_key,
                    md.metric_unit,
                    md.value_type,
                    p.value_double,
                    p.value_int,
                    p.value_bool,
                    p.event_time_ns
                FROM device_metric_points p
                JOIN devices d ON d.device_pk = p.device_pk
                JOIN device_boot_sessions b ON b.boot_pk = p.boot_pk
                JOIN metric_definitions md ON md.metric_pk = p.metric_pk
                WHERE d.device_id = $1
                ORDER BY p.sequence ASC, p.metric_index ASC
                "#,
                &[&device_id],
            )
            .unwrap();
        if rows.len() as i64 >= expected_count {
            return rows
                .into_iter()
                .map(|row| {
                    (
                        row.get(0),
                        row.get(1),
                        row.get::<_, i64>(2),
                        row.get::<_, i32>(3),
                        row.get(4),
                        row.get::<_, Option<String>>(5).unwrap_or_default(),
                        row.get(6),
                        row.get(7),
                        row.get(8),
                        row.get(9),
                        row.get::<_, Option<i64>>(10).unwrap_or_default(),
                    )
                })
                .collect();
        }
        sleep(Duration::from_secs(2));
    }
    panic!("timed out waiting for metric rows for {device_id}");
}

#[allow(clippy::type_complexity)]
fn wait_for_signal_frame_rows(
    device_id: &str,
    expected_count: i64,
) -> Vec<(
    String,
    String,
    i64,
    String,
    String,
    String,
    i64,
    i32,
    i32,
    i64,
    String,
)> {
    let deadline = Instant::now() + Duration::from_secs(120);
    while Instant::now() < deadline {
        let mut db = Client::connect(POSTGRES_DSN, NoTls).unwrap();
        let rows = db
            .query(
                r#"
                SELECT
                    d.device_id,
                    b.boot_id,
                    f.sequence,
                    sd.stream_key,
                    sd.encoding,
                    sd.layout,
                    f.sample_interval_ns,
                    f.sample_count,
                    f.samples_size,
                    f.event_time_ns,
                    sd.channels_json
                FROM device_signal_frames f
                JOIN devices d ON d.device_pk = f.device_pk
                JOIN device_boot_sessions b ON b.boot_pk = f.boot_pk
                JOIN signal_stream_definitions sd ON sd.signal_pk = f.signal_pk
                WHERE d.device_id = $1
                ORDER BY f.sequence ASC
                "#,
                &[&device_id],
            )
            .unwrap();
        if rows.len() as i64 >= expected_count {
            return rows
                .into_iter()
                .map(|row| {
                    (
                        row.get(0),
                        row.get(1),
                        row.get::<_, i64>(2),
                        row.get(3),
                        row.get(4),
                        row.get(5),
                        row.get::<_, i64>(6),
                        row.get::<_, i32>(7),
                        row.get::<_, i32>(8),
                        row.get::<_, Option<i64>>(9).unwrap_or_default(),
                        row.get(10),
                    )
                })
                .collect();
        }
        sleep(Duration::from_secs(2));
    }
    panic!("timed out waiting for signal frame rows for {device_id}");
}

#[test]
fn rust_client_uploads_metric_status_and_signal_frame_to_postgres() {
    if std::env::var("AETUS_SKIP_E2E").as_deref() == Ok("1") {
        return;
    }

    docker_compose(&["down", "-v", "--remove-orphans"]);
    if std::env::var("AETUS_RUST_E2E_BUILD").as_deref() == Ok("1") {
        docker_compose(&["up", "-d", "--build"]);
    } else {
        docker_compose(&["up", "-d"]);
    }

    let result = std::panic::catch_unwind(|| {
        wait_for_http(&format!("{INGEST_API_URL}/v1/healthz"));
        wait_for_http(&format!("{KAFKA_CONNECT_URL}/"));

        let provision_response = blocking::Client::new()
            .post(format!("{INGEST_API_URL}/v1/provision"))
            .bearer_auth("bootstrap_shared_token")
            .json(&json!({
                "hardware_id": "esp32c5-a1b2c3d4e5f6",
                "model": "rust-client",
                "firmware_version": 77,
                "site_code": "sdk-e2e",
            }))
            .send()
            .unwrap();
        assert_eq!(
            provision_response.status().as_u16(),
            201,
            "{}",
            provision_response.text().unwrap_or_default()
        );
        let provision_body: serde_json::Value = provision_response.json().unwrap();
        let device_id = provision_body["device_id"].as_str().unwrap().to_string();
        let token = provision_body["access_token"].as_str().unwrap().to_string();
        let boot_id = "boot-rust-client-e2e";

        let mut client =
            AetusIngestClient::new(INGEST_API_URL, &device_id, token, boot_id, 77).unwrap();
        let metric_response = client
            .send_metrics(
                vec![
                    metric("temperature", 23.5_f64, "celsius").unwrap(),
                    metric("battery_mv", 3988_i64, "mV").unwrap(),
                    metric("motion_detected", true, "").unwrap(),
                ],
                Some(1_822_345_678_000_000_000),
            )
            .unwrap();
        let status_response = client
            .send_status(
                DeviceStatus::Online,
                -57,
                123456,
                "power_on",
                Some(1_822_345_679_000_000_000),
            )
            .unwrap();
        let signal_samples = pack_signal_samples_f32(
            &[
                vec![0.1, 0.2, 0.3],
                vec![0.4, 0.5, 0.6],
                vec![0.7, 0.8, 0.9],
                vec![1.0, 1.1, 1.2],
            ],
            SignalLayout::Interleaved,
        )
        .unwrap();
        let signal_response = client
            .send_signal_frame(
                "rust.imu.accel",
                5_000_000,
                vec![
                    SignalChannelSpec::new("accel_x", "g").unwrap(),
                    SignalChannelSpec::new("accel_y", "g").unwrap(),
                    SignalChannelSpec::new("accel_z", "g").unwrap(),
                ],
                signal_samples,
                Some(1_822_345_680_000_000_000),
            )
            .unwrap();

        assert_eq!(metric_response.sequence, 0);
        assert_eq!(status_response.sequence, 1);
        assert_eq!(signal_response.sequence, 2);

        let raw_rows = wait_for_raw_rows(&device_id, 3);
        assert_eq!(raw_rows[0].0, device_id);
        assert_eq!(
            raw_rows.iter().map(|row| row.2).collect::<Vec<_>>(),
            vec![0, 1, 2]
        );
        assert!(raw_rows.iter().all(|row| row.1 == boot_id));
        assert_eq!(raw_rows[0].4, 1_822_345_678_000_000_000);
        assert_eq!(raw_rows[1].4, 1_822_345_679_000_000_000);
        assert_eq!(raw_rows[2].4, 1_822_345_680_000_000_000);
        assert!(raw_rows[0].5.contains("\"kind\":\"metric_set\""));
        assert!(raw_rows[1].5.contains("\"reboot_reason\":\"power_on\""));
        assert!(raw_rows[2].5.contains("\"kind\":\"signal_frame\""));

        let metric_rows = wait_for_metric_rows(&device_id, 3);
        let temperature = metric_rows
            .iter()
            .find(|row| row.4 == "temperature")
            .unwrap();
        assert_eq!(temperature.0, device_id);
        assert_eq!(temperature.1, boot_id);
        assert_eq!(temperature.2, 0);
        assert_eq!(temperature.3, 0);
        assert_eq!(temperature.5, "celsius");
        assert_eq!(temperature.6, "double");
        assert_eq!(temperature.7, Some(23.5));
        let battery = metric_rows
            .iter()
            .find(|row| row.4 == "battery_mv")
            .unwrap();
        assert_eq!(battery.6, "int");
        assert_eq!(battery.8, Some(3988));
        let motion = metric_rows
            .iter()
            .find(|row| row.4 == "motion_detected")
            .unwrap();
        assert_eq!(motion.6, "bool");
        assert_eq!(motion.9, Some(true));
        assert!(metric_rows
            .iter()
            .all(|row| row.10 == 1_822_345_678_000_000_000));

        let signal_rows = wait_for_signal_frame_rows(&device_id, 1);
        let signal = &signal_rows[0];
        assert_eq!(signal.0, device_id);
        assert_eq!(signal.1, boot_id);
        assert_eq!(signal.2, 2);
        assert_eq!(signal.3, "rust.imu.accel");
        assert_eq!(signal.4, "float32_le");
        assert_eq!(signal.5, "interleaved");
        assert_eq!(signal.6, 5_000_000);
        assert_eq!(signal.7, 4);
        assert_eq!(signal.8, 48);
        assert_eq!(signal.9, 1_822_345_680_000_000_000);
        assert!(signal.10.contains("accel_x"));
        assert!(signal.10.contains("accel_y"));
        assert!(signal.10.contains("accel_z"));
    });

    docker_compose(&["down", "-v", "--remove-orphans"]);
    if let Err(payload) = result {
        std::panic::resume_unwind(payload);
    }
}
