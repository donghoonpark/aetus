use crate::repository::{PendingDelivery, Repository};
use crate::webhook::{now_unix_seconds, sign_payload};
use reqwest::Client;
use std::time::Instant;
use tokio::time::{sleep, Duration};
use tracing::{error, info};

pub async fn dispatch_once(
    repo: &Repository,
    client: &Client,
    limit: i64,
) -> anyhow::Result<usize> {
    let deliveries = repo.claim_pending_deliveries(limit).await?;
    let mut delivered = 0;
    for delivery in deliveries {
        if send_delivery(repo, client, delivery).await? {
            delivered += 1;
        }
    }
    Ok(delivered)
}

async fn send_delivery(
    repo: &Repository,
    client: &Client,
    delivery: PendingDelivery,
) -> anyhow::Result<bool> {
    let body = serde_json::to_vec(&delivery.payload_json)?;
    let timestamp = now_unix_seconds();
    let signature = sign_payload(&delivery.secret, timestamp, &body)?;
    let started = Instant::now();
    let timeout = Duration::from_secs_f64(delivery.timeout_seconds.max(0.1));

    let result = client
        .post(&delivery.url)
        .timeout(timeout)
        .header("content-type", "application/json")
        .header("x-aetus-event-id", delivery.event_id.to_string())
        .header("x-aetus-timestamp", timestamp.to_string())
        .header("x-aetus-signature", signature)
        .body(body)
        .send()
        .await;

    let duration_ms = started.elapsed().as_millis().min(i32::MAX as u128) as i32;
    match result {
        Ok(response) if response.status().is_success() => {
            repo.record_delivery_success(&delivery, response.status().as_u16() as i32, duration_ms)
                .await?;
            Ok(true)
        }
        Ok(response) => {
            repo.record_delivery_failure(
                &delivery,
                Some(response.status().as_u16() as i32),
                format!("webhook returned {}", response.status()),
                duration_ms,
            )
            .await?;
            Ok(false)
        }
        Err(exc) => {
            repo.record_delivery_failure(&delivery, None, exc.to_string(), duration_ms)
                .await?;
            Ok(false)
        }
    }
}

pub async fn run_dispatcher_loop(repo: Repository, poll_interval: Duration) -> anyhow::Result<()> {
    let client = Client::new();
    info!(
        interval_seconds = poll_interval.as_secs(),
        "starting anomaly webhook dispatcher loop"
    );
    loop {
        match dispatch_once(&repo, &client, 100).await {
            Ok(delivered) => info!(delivered, "webhook dispatcher cycle completed"),
            Err(exc) => error!(error = ?exc, "webhook dispatcher cycle failed"),
        }
        sleep(poll_interval).await;
    }
}
