use crate::models::{
    CreateJobRequest, EventResponse, JobResponse, MetricPoint, NumericWindow,
    WebhookEndpointRequest, WebhookEndpointResponse, WindowAnchorConfig,
};
use chrono::{DateTime, Duration, Utc};
use serde::Deserialize;
use serde_json::Value;
use sqlx::postgres::PgPoolOptions;
use sqlx::{PgPool, Row};
use uuid::Uuid;

#[derive(Clone)]
pub struct Repository {
    pool: PgPool,
}

#[derive(Debug, Clone)]
pub struct JobRecord {
    pub job_id: i64,
    pub job_key: String,
    pub device_selector: Value,
    pub stream_selector: Value,
    pub detector_type: String,
    pub detector_config: Value,
    pub window_seconds: i32,
    pub step_seconds: i32,
    pub lookback_seconds: i32,
    pub severity: String,
}

#[derive(Debug, Clone)]
pub struct EventInsert {
    pub job_id: i64,
    pub device_id: String,
    pub stream_key: String,
    pub channel_key: Option<String>,
    pub window_start: DateTime<Utc>,
    pub window_end: DateTime<Utc>,
    pub severity: String,
    pub score: f64,
    pub threshold: f64,
    pub details: Value,
    pub detector_type: String,
    pub detector_version: String,
}

#[derive(Debug, Clone)]
pub struct PendingDelivery {
    pub outbox_id: i64,
    pub endpoint_id: i64,
    pub event_id: Uuid,
    pub url: String,
    pub secret: String,
    pub payload_json: Value,
    pub attempt_count: i32,
    pub max_attempts: i32,
    pub timeout_seconds: f64,
}

impl Repository {
    pub async fn connect(dsn: &str) -> anyhow::Result<Self> {
        let pool = PgPoolOptions::new()
            .max_connections(10)
            .connect(dsn)
            .await?;
        Ok(Self { pool })
    }

    pub async fn ready(&self) -> anyhow::Result<()> {
        sqlx::query("SELECT 1").execute(&self.pool).await?;
        Ok(())
    }

    pub async fn create_job(&self, request: CreateJobRequest) -> anyhow::Result<JobResponse> {
        let device_selector = serde_json::to_value(request.device_selector)?;
        let stream_selector = serde_json::to_value(request.stream_selector)?;
        let row = sqlx::query(
            r#"
            INSERT INTO anomaly_jobs(
                job_key,
                enabled,
                device_selector,
                stream_selector,
                detector_type,
                detector_config,
                window_seconds,
                step_seconds,
                lookback_seconds,
                severity
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (job_key) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                device_selector = EXCLUDED.device_selector,
                stream_selector = EXCLUDED.stream_selector,
                detector_type = EXCLUDED.detector_type,
                detector_config = EXCLUDED.detector_config,
                window_seconds = EXCLUDED.window_seconds,
                step_seconds = EXCLUDED.step_seconds,
                lookback_seconds = EXCLUDED.lookback_seconds,
                severity = EXCLUDED.severity,
                updated_at = NOW()
            RETURNING
                job_id, job_key, enabled, device_selector, stream_selector, detector_type,
                detector_config, window_seconds, step_seconds, lookback_seconds, severity,
                created_at, updated_at
            "#,
        )
        .bind(request.job_key)
        .bind(request.enabled)
        .bind(device_selector)
        .bind(stream_selector)
        .bind(request.detector_type)
        .bind(request.detector_config)
        .bind(request.window_seconds)
        .bind(request.step_seconds)
        .bind(request.lookback_seconds)
        .bind(request.severity)
        .fetch_one(&self.pool)
        .await?;
        let job_id: i64 = row.get("job_id");
        sqlx::query(
            "INSERT INTO anomaly_job_state(job_id, updated_at) VALUES ($1, NOW()) ON CONFLICT (job_id) DO NOTHING",
        )
        .bind(job_id)
        .execute(&self.pool)
        .await?;
        Ok(job_response(row))
    }

    pub async fn list_jobs(&self) -> anyhow::Result<Vec<JobResponse>> {
        let rows = sqlx::query(
            r#"
            SELECT
                job_id, job_key, enabled, device_selector, stream_selector, detector_type,
                detector_config, window_seconds, step_seconds, lookback_seconds, severity,
                created_at, updated_at
            FROM anomaly_jobs
            ORDER BY job_id ASC
            "#,
        )
        .fetch_all(&self.pool)
        .await?;
        Ok(rows.into_iter().map(job_response).collect())
    }

    pub async fn enabled_jobs(&self) -> anyhow::Result<Vec<JobRecord>> {
        let rows = sqlx::query(
            r#"
            SELECT
                job_id, job_key, enabled, device_selector, stream_selector, detector_type,
                detector_config, window_seconds, step_seconds, lookback_seconds, severity
            FROM anomaly_jobs
            WHERE enabled = TRUE
            ORDER BY job_id ASC
            "#,
        )
        .fetch_all(&self.pool)
        .await?;
        Ok(rows.into_iter().map(job_record).collect())
    }

    pub async fn numeric_windows(
        &self,
        device_id: &str,
        stream_key: &str,
        channels: &[String],
        window_seconds: i32,
        max_points: usize,
    ) -> anyhow::Result<Vec<NumericWindow>> {
        let metric = self
            .metric_window(device_id, stream_key, window_seconds, max_points)
            .await?;
        if !metric.points.is_empty() {
            return Ok(vec![metric]);
        }
        self.signal_windows(device_id, stream_key, channels, window_seconds, max_points)
            .await
    }

    pub async fn numeric_windows_between(
        &self,
        device_id: &str,
        stream_key: &str,
        channels: &[String],
        window_start: DateTime<Utc>,
        window_end: DateTime<Utc>,
        max_points: usize,
    ) -> anyhow::Result<Vec<NumericWindow>> {
        let metric = self
            .metric_window_between(device_id, stream_key, window_start, window_end, max_points)
            .await?;
        if !metric.points.is_empty() {
            return Ok(vec![metric]);
        }
        self.signal_windows_between(
            device_id,
            stream_key,
            channels,
            window_start,
            window_end,
            max_points,
        )
        .await
    }

    pub async fn anchor_event_times(
        &self,
        device_id: &str,
        anchor: &WindowAnchorConfig,
    ) -> anyhow::Result<Vec<DateTime<Utc>>> {
        let rows = sqlx::query(
            r#"
            SELECT p.event_time
            FROM device_metric_points p
            JOIN devices d ON d.device_pk = p.device_pk
            JOIN metric_definitions md ON md.metric_pk = p.metric_pk
            WHERE d.device_id = $1
              AND md.metric_key = $2
              AND ($4::text IS NULL OR p.value_string = $4)
              AND ($5::boolean IS NULL OR p.value_bool = $5)
              AND ($6::bigint IS NULL OR p.value_int = $6)
              AND ($7::double precision IS NULL OR p.value_double = $7)
            ORDER BY p.event_time DESC
            LIMIT $3
            "#,
        )
        .bind(device_id)
        .bind(&anchor.stream)
        .bind(anchor.max_events as i64)
        .bind(&anchor.value_string)
        .bind(anchor.value_bool)
        .bind(anchor.value_int)
        .bind(anchor.value_double)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows.into_iter().map(|row| row.get("event_time")).collect())
    }

    async fn metric_window(
        &self,
        device_id: &str,
        stream_key: &str,
        window_seconds: i32,
        max_points: usize,
    ) -> anyhow::Result<NumericWindow> {
        let latest_row = sqlx::query(
            r#"
            SELECT MAX(p.event_time) AS latest_event_time
            FROM device_metric_points p
            JOIN devices d ON d.device_pk = p.device_pk
            JOIN metric_definitions md ON md.metric_pk = p.metric_pk
            WHERE d.device_id = $1
              AND md.metric_key = $2
              AND (
                p.value_double IS NOT NULL
                OR p.value_int IS NOT NULL
                OR p.value_bool IS NOT NULL
              )
            "#,
        )
        .bind(device_id)
        .bind(stream_key)
        .fetch_one(&self.pool)
        .await?;
        let Some(window_end) =
            latest_row.try_get::<Option<DateTime<Utc>>, _>("latest_event_time")?
        else {
            let now = Utc::now();
            return Ok(NumericWindow {
                source_kind: "metric".to_string(),
                channel_key: None,
                window_start: now,
                window_end: now,
                points: Vec::new(),
                truncated: false,
            });
        };
        let window_start = window_end - Duration::seconds(window_seconds as i64);
        self.metric_window_between(device_id, stream_key, window_start, window_end, max_points)
            .await
    }

    async fn metric_window_between(
        &self,
        device_id: &str,
        stream_key: &str,
        window_start: DateTime<Utc>,
        window_end: DateTime<Utc>,
        max_points: usize,
    ) -> anyhow::Result<NumericWindow> {
        let rows = sqlx::query(
            r#"
            SELECT
                p.event_time,
                COALESCE(
                    p.value_double,
                    p.value_int::double precision,
                    CASE WHEN p.value_bool IS TRUE THEN 1.0 WHEN p.value_bool IS FALSE THEN 0.0 ELSE NULL END
                ) AS value
            FROM device_metric_points p
            JOIN devices d ON d.device_pk = p.device_pk
            JOIN metric_definitions md ON md.metric_pk = p.metric_pk
            WHERE d.device_id = $1
              AND md.metric_key = $2
              AND p.event_time >= $3
              AND p.event_time <= $4
              AND (
                p.value_double IS NOT NULL
                OR p.value_int IS NOT NULL
                OR p.value_bool IS NOT NULL
              )
            ORDER BY p.event_time ASC
            LIMIT $5
            "#,
        )
        .bind(device_id)
        .bind(stream_key)
        .bind(window_start)
        .bind(window_end)
        .bind(max_points as i64 + 1)
        .fetch_all(&self.pool)
        .await?;
        let mut points = rows
            .into_iter()
            .filter_map(|row| {
                let event_time: DateTime<Utc> = row.get("event_time");
                let value: Option<f64> = row.get("value");
                value.map(|value| MetricPoint { event_time, value })
            })
            .collect::<Vec<_>>();
        let truncated = points.len() > max_points;
        points.truncate(max_points);
        Ok(NumericWindow {
            source_kind: "metric".to_string(),
            channel_key: None,
            window_start,
            window_end,
            points,
            truncated,
        })
    }

    async fn signal_windows(
        &self,
        device_id: &str,
        stream_key: &str,
        selected_channels: &[String],
        window_seconds: i32,
        max_points: usize,
    ) -> anyhow::Result<Vec<NumericWindow>> {
        let latest_row = sqlx::query(
            r#"
            SELECT MAX(
                f.event_time + make_interval(
                    secs => (
                        f.sample_interval_ns::double precision
                        * GREATEST(f.sample_count - 1, 0)::double precision
                    ) / 1000000000.0
                )
            ) AS latest_event_time
            FROM device_signal_frames f
            JOIN devices d ON d.device_pk = f.device_pk
            JOIN signal_stream_definitions sd ON sd.signal_pk = f.signal_pk
            WHERE d.device_id = $1
              AND sd.stream_key = $2
            "#,
        )
        .bind(device_id)
        .bind(stream_key)
        .fetch_one(&self.pool)
        .await?;
        let Some(window_end) =
            latest_row.try_get::<Option<DateTime<Utc>>, _>("latest_event_time")?
        else {
            return Ok(Vec::new());
        };
        let window_start = window_end - Duration::seconds(window_seconds as i64);
        self.signal_windows_between(
            device_id,
            stream_key,
            selected_channels,
            window_start,
            window_end,
            max_points,
        )
        .await
    }

    async fn signal_windows_between(
        &self,
        device_id: &str,
        stream_key: &str,
        selected_channels: &[String],
        window_start: DateTime<Utc>,
        window_end: DateTime<Utc>,
        max_points: usize,
    ) -> anyhow::Result<Vec<NumericWindow>> {
        let rows = sqlx::query(
            r#"
            SELECT
                f.event_time,
                f.sample_interval_ns,
                f.sample_count,
                f.samples,
                sd.encoding,
                sd.layout,
                sd.channels_json
            FROM device_signal_frames f
            JOIN devices d ON d.device_pk = f.device_pk
            JOIN signal_stream_definitions sd ON sd.signal_pk = f.signal_pk
            WHERE d.device_id = $1
              AND sd.stream_key = $2
              AND f.event_time <= $4
              AND (
                f.event_time >= $3
                OR f.event_time + make_interval(
                    secs => (
                        f.sample_interval_ns::double precision
                        * GREATEST(f.sample_count - 1, 0)::double precision
                    ) / 1000000000.0
                ) >= $3
              )
            ORDER BY f.event_time ASC
            "#,
        )
        .bind(device_id)
        .bind(stream_key)
        .bind(window_start)
        .bind(window_end)
        .fetch_all(&self.pool)
        .await?;

        let mut windows = Vec::<NumericWindow>::new();
        for row in rows {
            let channels_json: String = row.get("channels_json");
            let frame = SignalFrameRow {
                event_time: row.get("event_time"),
                sample_interval_ns: row.get("sample_interval_ns"),
                sample_count: row.get("sample_count"),
                samples: row.get("samples"),
                encoding: row.get("encoding"),
                layout: row.get("layout"),
                channels: serde_json::from_str(&channels_json)?,
            };
            append_signal_frame_points(
                &mut windows,
                frame,
                selected_channels,
                window_start,
                window_end,
                max_points,
            )?;
        }
        Ok(windows)
    }

    pub async fn upsert_detection_event(&self, event: EventInsert) -> anyhow::Result<Uuid> {
        sqlx::query(
            r#"
            INSERT INTO anomaly_scores(
                job_id, device_id, stream_key, channel_key, channel_key_norm, window_start, window_end,
                score, threshold, severity, detector_type, detector_version, details_json
            )
            VALUES ($1,$2,$3,$4,COALESCE($4,''),$5,$6,$7,$8,$9,$10,$11,$12)
            ON CONFLICT (job_id, device_id, stream_key, channel_key_norm, window_start, window_end)
            DO UPDATE SET
                score = EXCLUDED.score,
                threshold = EXCLUDED.threshold,
                severity = EXCLUDED.severity,
                details_json = EXCLUDED.details_json
            "#,
        )
        .bind(event.job_id)
        .bind(&event.device_id)
        .bind(&event.stream_key)
        .bind(&event.channel_key)
        .bind(event.window_start)
        .bind(event.window_end)
        .bind(event.score)
        .bind(event.threshold)
        .bind(&event.severity)
        .bind(&event.detector_type)
        .bind(&event.detector_version)
        .bind(&event.details)
        .execute(&self.pool)
        .await?;

        let title = if let Some(channel_key) = &event.channel_key {
            format!(
                "{}:{} anomaly on {}",
                event.stream_key, channel_key, event.device_id
            )
        } else {
            format!("{} anomaly on {}", event.stream_key, event.device_id)
        };
        let summary = format!(
            "threshold rule crossed: score {:.3}, threshold {:.3}",
            event.score, event.threshold
        );
        let row = sqlx::query(
            r#"
            INSERT INTO anomaly_events(
                job_id, device_id, stream_key, channel_key, channel_key_norm, event_start, event_end,
                severity, status, score, threshold, title, summary, details_json
            )
            VALUES ($1,$2,$3,$4,COALESCE($4,''),$5,$6,$7,'open',$8,$9,$10,$11,$12)
            ON CONFLICT (job_id, device_id, stream_key, channel_key_norm, event_start)
            DO UPDATE SET
                event_end = GREATEST(anomaly_events.event_end, EXCLUDED.event_end),
                last_seen_at = NOW(),
                updated_at = NOW(),
                score = GREATEST(anomaly_events.score, EXCLUDED.score),
                threshold = EXCLUDED.threshold,
                summary = EXCLUDED.summary,
                details_json = EXCLUDED.details_json
            RETURNING event_id
            "#,
        )
        .bind(event.job_id)
        .bind(&event.device_id)
        .bind(&event.stream_key)
        .bind(&event.channel_key)
        .bind(event.window_start)
        .bind(event.window_end)
        .bind(&event.severity)
        .bind(event.score)
        .bind(event.threshold)
        .bind(title)
        .bind(summary)
        .bind(&event.details)
        .fetch_one(&self.pool)
        .await?;
        let event_id: Uuid = row.get("event_id");
        self.enqueue_webhooks(event_id).await?;
        Ok(event_id)
    }

    async fn enqueue_webhooks(&self, event_id: Uuid) -> anyhow::Result<()> {
        let rows = sqlx::query(
            r#"
            SELECT
                e.endpoint_id,
                jsonb_build_object(
                    'event_id', ev.event_id,
                    'job_id', ev.job_id,
                    'device_id', ev.device_id,
                    'stream_key', ev.stream_key,
                    'channel_key', ev.channel_key,
                    'severity', ev.severity,
                    'status', ev.status,
                    'window', jsonb_build_object('from', ev.event_start, 'to', ev.event_end),
                    'score', ev.score,
                    'threshold', ev.threshold,
                    'summary', ev.summary
                ) AS payload_json
            FROM webhook_endpoints e
            JOIN anomaly_events ev ON ev.event_id = $1
            WHERE e.enabled = TRUE
            "#,
        )
        .bind(event_id)
        .fetch_all(&self.pool)
        .await?;
        for row in rows {
            let endpoint_id: i64 = row.get("endpoint_id");
            let payload_json: Value = row.get("payload_json");
            sqlx::query(
                r#"
                INSERT INTO webhook_outbox(endpoint_id, event_id, payload_json)
                VALUES ($1,$2,$3)
                ON CONFLICT (endpoint_id, event_id) DO NOTHING
                "#,
            )
            .bind(endpoint_id)
            .bind(event_id)
            .bind(payload_json)
            .execute(&self.pool)
            .await?;
        }
        Ok(())
    }

    pub async fn list_events(&self, limit: i64) -> anyhow::Result<Vec<EventResponse>> {
        let rows = sqlx::query(
            r#"
            SELECT
                event_id, job_id, device_id, stream_key, channel_key, event_start, event_end,
                severity, status, score, threshold, title, summary, details_json
            FROM anomaly_events
            ORDER BY event_end DESC
            LIMIT $1
            "#,
        )
        .bind(limit)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows.into_iter().map(event_response).collect())
    }

    pub async fn create_webhook_endpoint(
        &self,
        request: WebhookEndpointRequest,
    ) -> anyhow::Result<WebhookEndpointResponse> {
        let row = sqlx::query(
            r#"
            INSERT INTO webhook_endpoints(
                endpoint_key, enabled, url, secret, event_filter, max_attempts, timeout_seconds
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (endpoint_key) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                url = EXCLUDED.url,
                secret = EXCLUDED.secret,
                event_filter = EXCLUDED.event_filter,
                max_attempts = EXCLUDED.max_attempts,
                timeout_seconds = EXCLUDED.timeout_seconds,
                updated_at = NOW()
            RETURNING endpoint_id, endpoint_key, enabled, url, event_filter, max_attempts, timeout_seconds
            "#,
        )
        .bind(request.endpoint_key)
        .bind(request.enabled)
        .bind(request.url)
        .bind(request.secret)
        .bind(request.event_filter)
        .bind(request.max_attempts.unwrap_or(8))
        .bind(request.timeout_seconds.unwrap_or(5.0))
        .fetch_one(&self.pool)
        .await?;
        Ok(webhook_endpoint_response(row))
    }

    pub async fn list_webhook_endpoints(&self) -> anyhow::Result<Vec<WebhookEndpointResponse>> {
        let rows = sqlx::query(
            r#"
            SELECT endpoint_id, endpoint_key, enabled, url, event_filter, max_attempts, timeout_seconds
            FROM webhook_endpoints
            ORDER BY endpoint_id ASC
            "#,
        )
        .fetch_all(&self.pool)
        .await?;
        Ok(rows.into_iter().map(webhook_endpoint_response).collect())
    }

    pub async fn claim_pending_deliveries(
        &self,
        limit: i64,
    ) -> anyhow::Result<Vec<PendingDelivery>> {
        let rows = sqlx::query(
            r#"
            SELECT
                o.outbox_id, o.endpoint_id, o.event_id, e.url, e.secret, o.payload_json,
                o.attempt_count, e.max_attempts, e.timeout_seconds
            FROM webhook_outbox o
            JOIN webhook_endpoints e ON e.endpoint_id = o.endpoint_id
            WHERE o.status IN ('pending', 'retry')
              AND o.next_attempt_at <= NOW()
              AND e.enabled = TRUE
            ORDER BY o.next_attempt_at ASC, o.outbox_id ASC
            LIMIT $1
            "#,
        )
        .bind(limit)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows
            .into_iter()
            .map(|row| PendingDelivery {
                outbox_id: row.get("outbox_id"),
                endpoint_id: row.get("endpoint_id"),
                event_id: row.get("event_id"),
                url: row.get("url"),
                secret: row.get("secret"),
                payload_json: row.get("payload_json"),
                attempt_count: row.get("attempt_count"),
                max_attempts: row.get("max_attempts"),
                timeout_seconds: row.get("timeout_seconds"),
            })
            .collect())
    }

    pub async fn record_delivery_success(
        &self,
        delivery: &PendingDelivery,
        status_code: i32,
        duration_ms: i32,
    ) -> anyhow::Result<()> {
        self.insert_delivery(delivery, Some(status_code), true, None, duration_ms)
            .await?;
        sqlx::query(
            "UPDATE webhook_outbox SET status='delivered', attempt_count=attempt_count+1, last_attempt_at=NOW(), updated_at=NOW() WHERE outbox_id=$1",
        )
        .bind(delivery.outbox_id)
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    pub async fn record_delivery_failure(
        &self,
        delivery: &PendingDelivery,
        status_code: Option<i32>,
        error: String,
        duration_ms: i32,
    ) -> anyhow::Result<()> {
        self.insert_delivery(
            delivery,
            status_code,
            false,
            Some(error.clone()),
            duration_ms,
        )
        .await?;
        let next_status = if delivery.attempt_count + 1 >= delivery.max_attempts {
            "dead_letter"
        } else {
            "retry"
        };
        sqlx::query(
            r#"
            UPDATE webhook_outbox
            SET
                status = $2,
                attempt_count = attempt_count + 1,
                last_attempt_at = NOW(),
                next_attempt_at = NOW() + (($3 || ' seconds')::interval),
                last_error = $4,
                updated_at = NOW()
            WHERE outbox_id = $1
            "#,
        )
        .bind(delivery.outbox_id)
        .bind(next_status)
        .bind(crate::webhook::next_backoff_seconds(delivery.attempt_count).to_string())
        .bind(error)
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    async fn insert_delivery(
        &self,
        delivery: &PendingDelivery,
        status_code: Option<i32>,
        success: bool,
        error: Option<String>,
        duration_ms: i32,
    ) -> anyhow::Result<()> {
        sqlx::query(
            r#"
            INSERT INTO webhook_deliveries(
                outbox_id, endpoint_id, event_id, attempt_number, status_code, success, error, duration_ms
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            "#,
        )
        .bind(delivery.outbox_id)
        .bind(delivery.endpoint_id)
        .bind(delivery.event_id)
        .bind(delivery.attempt_count + 1)
        .bind(status_code)
        .bind(success)
        .bind(error)
        .bind(duration_ms)
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    pub async fn update_job_state_success(
        &self,
        job_id: i64,
        window_end: DateTime<Utc>,
    ) -> anyhow::Result<()> {
        sqlx::query(
            r#"
            INSERT INTO anomaly_job_state(job_id, last_window_end, heartbeat_at, updated_at)
            VALUES ($1,$2,NOW(),NOW())
            ON CONFLICT (job_id) DO UPDATE SET
                last_window_end = EXCLUDED.last_window_end,
                heartbeat_at = NOW(),
                last_error = NULL,
                updated_at = NOW()
            "#,
        )
        .bind(job_id)
        .bind(window_end)
        .execute(&self.pool)
        .await?;
        Ok(())
    }
}

#[derive(Debug, Deserialize)]
struct ChannelDef {
    key: String,
    #[serde(rename = "unit")]
    _unit: Option<String>,
    scale: Option<f64>,
    offset: Option<f64>,
}

struct SignalFrameRow {
    event_time: DateTime<Utc>,
    sample_interval_ns: i64,
    sample_count: i32,
    samples: Vec<u8>,
    encoding: String,
    layout: String,
    channels: Vec<ChannelDef>,
}

fn append_signal_frame_points(
    windows: &mut Vec<NumericWindow>,
    frame: SignalFrameRow,
    selected_channels: &[String],
    window_start: DateTime<Utc>,
    window_end: DateTime<Utc>,
    max_points: usize,
) -> anyhow::Result<()> {
    if frame.sample_count <= 0 || frame.channels.is_empty() {
        return Ok(());
    }
    let width = sample_width_bytes(&frame.encoding)?;
    let expected = frame.sample_count as usize * frame.channels.len() * width;
    if frame.samples.len() != expected {
        anyhow::bail!(
            "signal frame sample size mismatch: expected {}, got {}",
            expected,
            frame.samples.len()
        );
    }
    if !matches!(frame.layout.as_str(), "interleaved" | "planar") {
        anyhow::bail!("unsupported signal layout: {}", frame.layout);
    }

    for channel in &frame.channels {
        if !selected_channels.is_empty()
            && !selected_channels.iter().any(|item| item == &channel.key)
        {
            continue;
        }
        if !windows
            .iter()
            .any(|window| window.channel_key.as_deref() == Some(channel.key.as_str()))
        {
            windows.push(NumericWindow {
                source_kind: "signal_frame".to_string(),
                channel_key: Some(channel.key.clone()),
                window_start,
                window_end,
                points: Vec::new(),
                truncated: false,
            });
        }
    }

    for sample_index in 0..frame.sample_count as usize {
        let event_time = frame.event_time
            + Duration::microseconds((sample_index as i64 * frame.sample_interval_ns) / 1000);
        if event_time < window_start || event_time > window_end {
            continue;
        }
        for (channel_index, channel) in frame.channels.iter().enumerate() {
            if !selected_channels.is_empty()
                && !selected_channels.iter().any(|item| item == &channel.key)
            {
                continue;
            }
            let Some(window) = windows
                .iter_mut()
                .find(|window| window.channel_key.as_deref() == Some(channel.key.as_str()))
            else {
                continue;
            };
            if window.points.len() >= max_points {
                window.truncated = true;
                continue;
            }
            let value_index = if frame.layout == "interleaved" {
                sample_index * frame.channels.len() + channel_index
            } else {
                channel_index * frame.sample_count as usize + sample_index
            };
            let mut value = decode_sample(&frame.samples, value_index * width, &frame.encoding)?;
            if let Some(scale) = channel.scale {
                value *= scale;
            }
            if let Some(offset) = channel.offset {
                value += offset;
            }
            window.points.push(MetricPoint { event_time, value });
        }
    }
    Ok(())
}

fn sample_width_bytes(encoding: &str) -> anyhow::Result<usize> {
    match encoding {
        "float32_le" | "int32_le" => Ok(4),
        "int16_le" | "uint16_le" => Ok(2),
        _ => anyhow::bail!("unsupported signal encoding: {}", encoding),
    }
}

fn decode_sample(samples: &[u8], offset: usize, encoding: &str) -> anyhow::Result<f64> {
    Ok(match encoding {
        "float32_le" => f32::from_le_bytes(samples[offset..offset + 4].try_into()?) as f64,
        "int16_le" => i16::from_le_bytes(samples[offset..offset + 2].try_into()?) as f64,
        "uint16_le" => u16::from_le_bytes(samples[offset..offset + 2].try_into()?) as f64,
        "int32_le" => i32::from_le_bytes(samples[offset..offset + 4].try_into()?) as f64,
        _ => anyhow::bail!("unsupported signal encoding: {}", encoding),
    })
}

fn job_response(row: sqlx::postgres::PgRow) -> JobResponse {
    JobResponse {
        job_id: row.get("job_id"),
        job_key: row.get("job_key"),
        enabled: row.get("enabled"),
        device_selector: row.get("device_selector"),
        stream_selector: row.get("stream_selector"),
        detector_type: row.get("detector_type"),
        detector_config: row.get("detector_config"),
        window_seconds: row.get("window_seconds"),
        step_seconds: row.get("step_seconds"),
        lookback_seconds: row.get("lookback_seconds"),
        severity: row.get("severity"),
        created_at: row.get("created_at"),
        updated_at: row.get("updated_at"),
    }
}

fn job_record(row: sqlx::postgres::PgRow) -> JobRecord {
    JobRecord {
        job_id: row.get("job_id"),
        job_key: row.get("job_key"),
        device_selector: row.get("device_selector"),
        stream_selector: row.get("stream_selector"),
        detector_type: row.get("detector_type"),
        detector_config: row.get("detector_config"),
        window_seconds: row.get("window_seconds"),
        step_seconds: row.get("step_seconds"),
        lookback_seconds: row.get("lookback_seconds"),
        severity: row.get("severity"),
    }
}

fn event_response(row: sqlx::postgres::PgRow) -> EventResponse {
    EventResponse {
        event_id: row.get("event_id"),
        job_id: row.get("job_id"),
        device_id: row.get("device_id"),
        stream_key: row.get("stream_key"),
        channel_key: row.get("channel_key"),
        event_start: row.get("event_start"),
        event_end: row.get("event_end"),
        severity: row.get("severity"),
        status: row.get("status"),
        score: row.get("score"),
        threshold: row.get("threshold"),
        title: row.get("title"),
        summary: row.get("summary"),
        details_json: row.get("details_json"),
    }
}

fn webhook_endpoint_response(row: sqlx::postgres::PgRow) -> WebhookEndpointResponse {
    WebhookEndpointResponse {
        endpoint_id: row.get("endpoint_id"),
        endpoint_key: row.get("endpoint_key"),
        enabled: row.get("enabled"),
        url: row.get("url"),
        event_filter: row.get("event_filter"),
        max_attempts: row.get("max_attempts"),
        timeout_seconds: row.get("timeout_seconds"),
    }
}

pub fn selector_from_value(value: &Value) -> anyhow::Result<crate::models::Selector> {
    Ok(
        serde_json::from_value(value.clone()).unwrap_or(crate::models::Selector {
            devices: Vec::new(),
            streams: Vec::new(),
            channels: Vec::new(),
        }),
    )
}
