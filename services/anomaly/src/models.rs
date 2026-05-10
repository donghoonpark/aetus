use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use utoipa::ToSchema;
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize, ToSchema)]
pub struct Selector {
    #[serde(default)]
    pub devices: Vec<String>,
    #[serde(default)]
    pub streams: Vec<String>,
    #[serde(default)]
    pub channels: Vec<String>,
}

impl Selector {
    pub fn first_devices(&self) -> Vec<String> {
        self.devices
            .iter()
            .filter(|item| !item.trim().is_empty() && item.as_str() != "*")
            .cloned()
            .collect()
    }

    pub fn first_streams(&self) -> Vec<String> {
        self.streams
            .iter()
            .filter(|item| !item.trim().is_empty() && item.as_str() != "*")
            .cloned()
            .collect()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, ToSchema)]
pub struct ThresholdConfig {
    pub threshold: f64,
    #[serde(default = "default_operator")]
    pub operator: String,
}

fn default_operator() -> String {
    "gt".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize, ToSchema)]
pub struct CreateJobRequest {
    pub job_key: String,
    #[serde(default = "default_enabled")]
    pub enabled: bool,
    pub device_selector: Selector,
    pub stream_selector: Selector,
    #[serde(default = "default_detector")]
    pub detector_type: String,
    pub detector_config: Value,
    pub window_seconds: i32,
    #[serde(default = "default_step_seconds")]
    pub step_seconds: i32,
    #[serde(default)]
    pub lookback_seconds: i32,
    #[serde(default = "default_severity")]
    pub severity: String,
}

fn default_enabled() -> bool {
    true
}

fn default_detector() -> String {
    "threshold".to_string()
}

fn default_step_seconds() -> i32 {
    60
}

fn default_severity() -> String {
    "warning".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize, ToSchema)]
pub struct JobResponse {
    pub job_id: i64,
    pub job_key: String,
    pub enabled: bool,
    pub device_selector: Value,
    pub stream_selector: Value,
    pub detector_type: String,
    pub detector_config: Value,
    pub window_seconds: i32,
    pub step_seconds: i32,
    pub lookback_seconds: i32,
    pub severity: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, ToSchema)]
pub struct EventResponse {
    pub event_id: Uuid,
    pub job_id: i64,
    pub device_id: String,
    pub stream_key: String,
    pub channel_key: Option<String>,
    pub event_start: DateTime<Utc>,
    pub event_end: DateTime<Utc>,
    pub severity: String,
    pub status: String,
    pub score: f64,
    pub threshold: Option<f64>,
    pub title: String,
    pub summary: String,
    pub details_json: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, ToSchema)]
pub struct WebhookEndpointRequest {
    pub endpoint_key: String,
    #[serde(default = "default_enabled")]
    pub enabled: bool,
    pub url: String,
    pub secret: String,
    #[serde(default)]
    pub event_filter: Value,
    pub max_attempts: Option<i32>,
    pub timeout_seconds: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, ToSchema)]
pub struct WebhookEndpointResponse {
    pub endpoint_id: i64,
    pub endpoint_key: String,
    pub enabled: bool,
    pub url: String,
    pub event_filter: Value,
    pub max_attempts: i32,
    pub timeout_seconds: f64,
}

#[derive(Debug, Clone)]
pub struct MetricPoint {
    pub event_time: DateTime<Utc>,
    pub value: f64,
}

#[derive(Debug, Clone)]
pub struct DetectionResult {
    pub crossed: bool,
    pub score: f64,
    pub threshold: f64,
    pub details: Value,
}
