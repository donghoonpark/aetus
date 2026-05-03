use prost::Message;
use reqwest::blocking::Client as HttpClient;
use serde::Deserialize;
use std::fmt;
use std::time::{SystemTime, UNIX_EPOCH};
use uuid::Uuid;

pub mod generated {
    include!(concat!(env!("OUT_DIR"), "/aetus.ingest.v1.rs"));
}

use generated::ingest_event;
use generated::metric as metric_pb;
use generated::telemetry_payload;
use generated::{
    AlertPayload, DeviceStatus, EventType, IngestEvent, Metric, MetricSet, Severity, SignalChannel,
    SignalFrame, SignalSampleEncoding, SignalSampleLayout, StatusPayload, TelemetryPayload,
};

#[derive(Debug)]
pub enum Error {
    InvalidInput(String),
    Http(reqwest::Error),
    Rejected {
        status_code: u16,
        response_text: String,
    },
    Decode(reqwest::Error),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidInput(message) => write!(f, "invalid AETUS input: {message}"),
            Self::Http(error) => write!(f, "AETUS HTTP error: {error}"),
            Self::Rejected {
                status_code,
                response_text,
            } => write!(
                f,
                "AETUS ingest rejected upload with HTTP {status_code}: {response_text}"
            ),
            Self::Decode(error) => write!(f, "AETUS response decode error: {error}"),
        }
    }
}

impl std::error::Error for Error {}

#[derive(Debug, Clone, PartialEq)]
pub enum MetricValue {
    Int(i64),
    Double(f64),
    Bool(bool),
    String(String),
    Bytes(Vec<u8>),
}

impl From<i64> for MetricValue {
    fn from(value: i64) -> Self {
        Self::Int(value)
    }
}

impl From<i32> for MetricValue {
    fn from(value: i32) -> Self {
        Self::Int(i64::from(value))
    }
}

impl From<u32> for MetricValue {
    fn from(value: u32) -> Self {
        Self::Int(i64::from(value))
    }
}

impl From<f64> for MetricValue {
    fn from(value: f64) -> Self {
        Self::Double(value)
    }
}

impl From<f32> for MetricValue {
    fn from(value: f32) -> Self {
        Self::Double(f64::from(value))
    }
}

impl From<bool> for MetricValue {
    fn from(value: bool) -> Self {
        Self::Bool(value)
    }
}

impl From<&str> for MetricValue {
    fn from(value: &str) -> Self {
        Self::String(value.to_string())
    }
}

impl From<String> for MetricValue {
    fn from(value: String) -> Self {
        Self::String(value)
    }
}

impl From<Vec<u8>> for MetricValue {
    fn from(value: Vec<u8>) -> Self {
        Self::Bytes(value)
    }
}

impl From<&[u8]> for MetricValue {
    fn from(value: &[u8]) -> Self {
        Self::Bytes(value.to_vec())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignalEncoding {
    Float32Le,
    Int16Le,
    Uint16Le,
    Int32Le,
}

impl SignalEncoding {
    fn proto_value(self) -> i32 {
        match self {
            Self::Float32Le => SignalSampleEncoding::Float32Le as i32,
            Self::Int16Le => SignalSampleEncoding::Int16Le as i32,
            Self::Uint16Le => SignalSampleEncoding::Uint16Le as i32,
            Self::Int32Le => SignalSampleEncoding::Int32Le as i32,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignalLayout {
    Interleaved,
    Planar,
}

impl SignalLayout {
    fn proto_value(self) -> i32 {
        match self {
            Self::Interleaved => SignalSampleLayout::Interleaved as i32,
            Self::Planar => SignalSampleLayout::Planar as i32,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct SignalChannelSpec {
    pub key: String,
    pub unit: String,
    pub scale: Option<f32>,
    pub offset: Option<f32>,
}

impl SignalChannelSpec {
    pub fn new(key: impl Into<String>, unit: impl Into<String>) -> Result<Self, Error> {
        let key = key.into();
        if key.is_empty() {
            return Err(Error::InvalidInput(
                "signal channel key is required".to_string(),
            ));
        }
        Ok(Self {
            key,
            unit: unit.into(),
            scale: None,
            offset: None,
        })
    }

    pub fn scale(mut self, value: f32) -> Self {
        self.scale = Some(value);
        self
    }

    pub fn offset(mut self, value: f32) -> Self {
        self.offset = Some(value);
        self
    }
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct IngestResponse {
    pub request_id: String,
    pub status: String,
    pub device_id: String,
    pub sequence: u64,
}

#[derive(Debug, Clone)]
pub struct AetusIngestClient {
    base_url: String,
    device_id: String,
    token: String,
    boot_id: String,
    firmware_version: u32,
    sequence: u64,
    http: HttpClient,
}

impl AetusIngestClient {
    pub fn new(
        base_url: impl Into<String>,
        device_id: impl Into<String>,
        token: impl Into<String>,
        boot_id: impl Into<String>,
        firmware_version: u32,
    ) -> Result<Self, Error> {
        Self::with_sequence(base_url, device_id, token, boot_id, firmware_version, 0)
    }

    pub fn with_sequence(
        base_url: impl Into<String>,
        device_id: impl Into<String>,
        token: impl Into<String>,
        boot_id: impl Into<String>,
        firmware_version: u32,
        initial_sequence: u64,
    ) -> Result<Self, Error> {
        let device_id = device_id.into();
        let token = token.into();
        let boot_id = boot_id.into();
        if device_id.is_empty() {
            return Err(Error::InvalidInput("device_id is required".to_string()));
        }
        if token.is_empty() {
            return Err(Error::InvalidInput("token is required".to_string()));
        }
        if boot_id.is_empty() {
            return Err(Error::InvalidInput("boot_id is required".to_string()));
        }

        Ok(Self {
            base_url: base_url.into().trim_end_matches('/').to_string(),
            device_id,
            token,
            boot_id,
            firmware_version,
            sequence: initial_sequence,
            http: HttpClient::new(),
        })
    }

    pub fn generated_boot_id(prefix: &str) -> String {
        let now_ns = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or_default();
        format!("{prefix}-{now_ns}-{}", Uuid::new_v4().simple())
    }

    pub fn sequence(&self) -> u64 {
        self.sequence
    }

    pub fn send_metrics(
        &mut self,
        metrics: Vec<Metric>,
        timestamp_ns: Option<u64>,
    ) -> Result<IngestResponse, Error> {
        let event = build_metric_event(
            &self.device_id,
            self.sequence,
            &self.boot_id,
            self.firmware_version,
            0,
            timestamp_ns,
            metrics,
        )?;
        self.send_event(event)
    }

    pub fn send_status(
        &mut self,
        status: DeviceStatus,
        rssi: i32,
        free_heap: u32,
        reboot_reason: impl Into<String>,
        timestamp_ns: Option<u64>,
    ) -> Result<IngestResponse, Error> {
        let event = build_status_event(
            &self.device_id,
            self.sequence,
            &self.boot_id,
            self.firmware_version,
            0,
            timestamp_ns,
            status,
            rssi,
            free_heap,
            reboot_reason,
        )?;
        self.send_event(event)
    }

    pub fn send_alert(
        &mut self,
        code: impl Into<String>,
        severity: Severity,
        message: impl Into<String>,
        timestamp_ns: Option<u64>,
    ) -> Result<IngestResponse, Error> {
        let event = build_alert_event(
            &self.device_id,
            self.sequence,
            &self.boot_id,
            self.firmware_version,
            0,
            timestamp_ns,
            code,
            severity,
            message,
        )?;
        self.send_event(event)
    }

    pub fn send_signal_frame(
        &mut self,
        stream_key: impl Into<String>,
        sample_interval_ns: u64,
        channels: Vec<SignalChannelSpec>,
        samples: PackedSignalSamples,
        timestamp_ns: Option<u64>,
    ) -> Result<IngestResponse, Error> {
        let event = build_signal_frame_event(
            &self.device_id,
            self.sequence,
            &self.boot_id,
            self.firmware_version,
            0,
            timestamp_ns,
            stream_key,
            sample_interval_ns,
            channels,
            samples,
        )?;
        self.send_event(event)
    }

    pub fn send_event(&mut self, event: IngestEvent) -> Result<IngestResponse, Error> {
        let mut body = Vec::new();
        event
            .encode(&mut body)
            .map_err(|error| Error::InvalidInput(error.to_string()))?;

        let response = self
            .http
            .post(format!("{}/v1/ingest", self.base_url))
            .header("Content-Type", "application/x-protobuf")
            .header("X-Device-Id", event.device_id.clone())
            .bearer_auth(&self.token)
            .body(body)
            .send()
            .map_err(Error::Http)?;

        let status = response.status();
        if !status.is_success() {
            let response_text = response.text().unwrap_or_else(|_| String::new());
            return Err(Error::Rejected {
                status_code: status.as_u16(),
                response_text,
            });
        }

        let parsed = response.json::<IngestResponse>().map_err(Error::Decode)?;
        if event.device_id == self.device_id && event.sequence == self.sequence {
            self.sequence += 1;
        }
        Ok(parsed)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PackedSignalSamples {
    pub encoding: SignalEncoding,
    pub layout: SignalLayout,
    pub sample_count: u32,
    pub bytes: Vec<u8>,
}

pub fn metric(
    key: impl Into<String>,
    value: impl Into<MetricValue>,
    unit: impl Into<String>,
) -> Result<Metric, Error> {
    let key = key.into();
    if key.is_empty() {
        return Err(Error::InvalidInput("metric key is required".to_string()));
    }
    let value = match value.into() {
        MetricValue::Int(value) => metric_pb::Value::IntValue(value),
        MetricValue::Double(value) => metric_pb::Value::DoubleValue(value),
        MetricValue::Bool(value) => metric_pb::Value::BoolValue(value),
        MetricValue::String(value) => metric_pb::Value::StringValue(value),
        MetricValue::Bytes(value) => metric_pb::Value::BytesValue(value),
    };
    Ok(Metric {
        key,
        value: Some(value),
        unit: unit.into(),
    })
}

pub fn build_metric_event(
    device_id: &str,
    sequence: u64,
    boot_id: &str,
    firmware_version: u32,
    uptime_ms: u64,
    timestamp_ns: Option<u64>,
    metrics: Vec<Metric>,
) -> Result<IngestEvent, Error> {
    if metrics.is_empty() {
        return Err(Error::InvalidInput(
            "at least one metric is required".to_string(),
        ));
    }
    let mut event = base_event(
        device_id,
        sequence,
        EventType::Telemetry,
        boot_id,
        firmware_version,
        uptime_ms,
        timestamp_ns,
    )?;
    event.body = Some(ingest_event::Body::Telemetry(TelemetryPayload {
        payload: Some(telemetry_payload::Payload::MetricSet(MetricSet { metrics })),
    }));
    Ok(event)
}

pub fn build_status_event(
    device_id: &str,
    sequence: u64,
    boot_id: &str,
    firmware_version: u32,
    uptime_ms: u64,
    timestamp_ns: Option<u64>,
    status: DeviceStatus,
    rssi: i32,
    free_heap: u32,
    reboot_reason: impl Into<String>,
) -> Result<IngestEvent, Error> {
    let mut event = base_event(
        device_id,
        sequence,
        EventType::Status,
        boot_id,
        firmware_version,
        uptime_ms,
        timestamp_ns,
    )?;
    event.body = Some(ingest_event::Body::Status(StatusPayload {
        status: status as i32,
        rssi,
        free_heap,
        reboot_reason: reboot_reason.into(),
    }));
    Ok(event)
}

pub fn build_alert_event(
    device_id: &str,
    sequence: u64,
    boot_id: &str,
    firmware_version: u32,
    uptime_ms: u64,
    timestamp_ns: Option<u64>,
    code: impl Into<String>,
    severity: Severity,
    message: impl Into<String>,
) -> Result<IngestEvent, Error> {
    let mut event = base_event(
        device_id,
        sequence,
        EventType::Alert,
        boot_id,
        firmware_version,
        uptime_ms,
        timestamp_ns,
    )?;
    event.body = Some(ingest_event::Body::Alert(AlertPayload {
        code: code.into(),
        severity: severity as i32,
        message: message.into(),
    }));
    Ok(event)
}

pub fn build_signal_frame_event(
    device_id: &str,
    sequence: u64,
    boot_id: &str,
    firmware_version: u32,
    uptime_ms: u64,
    timestamp_ns: Option<u64>,
    stream_key: impl Into<String>,
    sample_interval_ns: u64,
    channels: Vec<SignalChannelSpec>,
    samples: PackedSignalSamples,
) -> Result<IngestEvent, Error> {
    let stream_key = stream_key.into();
    if stream_key.is_empty() {
        return Err(Error::InvalidInput("stream_key is required".to_string()));
    }
    if sample_interval_ns == 0 {
        return Err(Error::InvalidInput(
            "sample_interval_ns must be positive".to_string(),
        ));
    }
    if channels.is_empty() {
        return Err(Error::InvalidInput(
            "at least one signal channel is required".to_string(),
        ));
    }
    if samples.sample_count == 0 {
        return Err(Error::InvalidInput(
            "sample_count must be positive".to_string(),
        ));
    }

    let mut event = base_event(
        device_id,
        sequence,
        EventType::Telemetry,
        boot_id,
        firmware_version,
        uptime_ms,
        timestamp_ns,
    )?;
    let channels = channels
        .into_iter()
        .map(|channel| SignalChannel {
            key: channel.key,
            unit: channel.unit,
            scale: channel.scale,
            offset: channel.offset,
        })
        .collect();
    event.body = Some(ingest_event::Body::Telemetry(TelemetryPayload {
        payload: Some(telemetry_payload::Payload::SignalFrame(SignalFrame {
            stream_key,
            sample_interval_ns,
            sample_count: samples.sample_count,
            encoding: samples.encoding.proto_value(),
            layout: samples.layout.proto_value(),
            channels,
            samples: samples.bytes,
        })),
    }));
    Ok(event)
}

fn base_event(
    device_id: &str,
    sequence: u64,
    event_type: EventType,
    boot_id: &str,
    firmware_version: u32,
    uptime_ms: u64,
    timestamp_ns: Option<u64>,
) -> Result<IngestEvent, Error> {
    if device_id.is_empty() {
        return Err(Error::InvalidInput("device_id is required".to_string()));
    }
    if boot_id.is_empty() {
        return Err(Error::InvalidInput("boot_id is required".to_string()));
    }
    Ok(IngestEvent {
        schema_version: 1,
        device_id: device_id.to_string(),
        sequence,
        event_type: event_type as i32,
        boot_id: boot_id.to_string(),
        firmware_version,
        uptime_ms,
        timestamp_ns: timestamp_ns.unwrap_or_default(),
        body: None,
    })
}

pub fn pack_signal_samples_f32(
    samples: &[Vec<f32>],
    layout: SignalLayout,
) -> Result<PackedSignalSamples, Error> {
    pack_signal_samples(
        samples,
        SignalEncoding::Float32Le,
        layout,
        |value, bytes| bytes.extend_from_slice(&value.to_le_bytes()),
    )
}

pub fn pack_signal_samples_i16(
    samples: &[Vec<i16>],
    layout: SignalLayout,
) -> Result<PackedSignalSamples, Error> {
    pack_signal_samples(samples, SignalEncoding::Int16Le, layout, |value, bytes| {
        bytes.extend_from_slice(&value.to_le_bytes())
    })
}

pub fn pack_signal_samples_u16(
    samples: &[Vec<u16>],
    layout: SignalLayout,
) -> Result<PackedSignalSamples, Error> {
    pack_signal_samples(samples, SignalEncoding::Uint16Le, layout, |value, bytes| {
        bytes.extend_from_slice(&value.to_le_bytes())
    })
}

pub fn pack_signal_samples_i32(
    samples: &[Vec<i32>],
    layout: SignalLayout,
) -> Result<PackedSignalSamples, Error> {
    pack_signal_samples(samples, SignalEncoding::Int32Le, layout, |value, bytes| {
        bytes.extend_from_slice(&value.to_le_bytes())
    })
}

fn pack_signal_samples<T: Copy>(
    samples: &[Vec<T>],
    encoding: SignalEncoding,
    layout: SignalLayout,
    write_value: impl Fn(T, &mut Vec<u8>),
) -> Result<PackedSignalSamples, Error> {
    if samples.is_empty() {
        return Err(Error::InvalidInput(
            "at least one signal sample row is required".to_string(),
        ));
    }
    let channel_count = samples[0].len();
    if channel_count == 0 {
        return Err(Error::InvalidInput(
            "signal samples must contain at least one channel".to_string(),
        ));
    }
    if samples.iter().any(|row| row.len() != channel_count) {
        return Err(Error::InvalidInput(
            "all signal sample rows must have the same channel count".to_string(),
        ));
    }

    let mut bytes = Vec::new();
    match layout {
        SignalLayout::Interleaved => {
            for row in samples {
                for value in row {
                    write_value(*value, &mut bytes);
                }
            }
        }
        SignalLayout::Planar => {
            for channel_index in 0..channel_count {
                for row in samples {
                    write_value(row[channel_index], &mut bytes);
                }
            }
        }
    }

    Ok(PackedSignalSamples {
        encoding,
        layout,
        sample_count: samples.len() as u32,
        bytes,
    })
}
