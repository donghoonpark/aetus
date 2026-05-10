use std::env;
use std::net::SocketAddr;
use std::time::Duration;

#[derive(Debug, Clone)]
pub struct Settings {
    pub postgres_dsn: String,
    pub admin_token: String,
    pub bind_addr: SocketAddr,
    pub worker_id: String,
    pub poll_interval: Duration,
    pub webhook_max_attempts: i32,
}

impl Settings {
    pub fn from_env() -> anyhow::Result<Self> {
        let host = env::var("AETUS_ANOMALY_HOST").unwrap_or_else(|_| "0.0.0.0".to_string());
        let port = env::var("AETUS_ANOMALY_PORT")
            .ok()
            .and_then(|raw| raw.parse::<u16>().ok())
            .unwrap_or(8000);
        Ok(Self {
            postgres_dsn: env::var("AETUS_POSTGRES_DSN")
                .unwrap_or_else(|_| "postgresql://aetus:aetus@127.0.0.1:15432/aetus".to_string()),
            admin_token: env::var("AETUS_ANOMALY_ADMIN_TOKEN")
                .unwrap_or_else(|_| "change-me-anomaly-admin-token".to_string()),
            bind_addr: format!("{host}:{port}").parse()?,
            worker_id: env::var("AETUS_ANOMALY_WORKER_ID")
                .unwrap_or_else(|_| "anomaly-worker-1".to_string()),
            poll_interval: Duration::from_secs(
                env::var("AETUS_ANOMALY_POLL_INTERVAL_SECONDS")
                    .ok()
                    .and_then(|raw| raw.parse::<u64>().ok())
                    .unwrap_or(10),
            ),
            webhook_max_attempts: env::var("AETUS_ANOMALY_WEBHOOK_MAX_ATTEMPTS")
                .ok()
                .and_then(|raw| raw.parse::<i32>().ok())
                .unwrap_or(8),
        })
    }
}
