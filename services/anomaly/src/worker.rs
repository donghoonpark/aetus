use crate::detector::evaluate_threshold;
use crate::models::ThresholdConfig;
use crate::repository::{selector_from_value, EventInsert, Repository};
use serde::{Deserialize, Serialize};
use serde_json::json;
use tokio::time::{sleep, Duration};
use tracing::{error, info, warn};

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RunSummary {
    pub jobs_scanned: usize,
    pub windows_scanned: usize,
    pub events_created: usize,
    pub skipped_jobs: usize,
}

pub async fn run_detection_once(
    repo: &Repository,
    only_job_id: Option<i64>,
) -> anyhow::Result<RunSummary> {
    let jobs = repo.enabled_jobs().await?;
    let mut summary = RunSummary::default();

    for job in jobs {
        if only_job_id.is_some_and(|target| target != job.job_id) {
            continue;
        }
        summary.jobs_scanned += 1;

        if job.detector_type != "threshold" {
            summary.skipped_jobs += 1;
            warn!(job_id = job.job_id, detector_type = %job.detector_type, "unsupported detector type");
            continue;
        }

        let device_selector = selector_from_value(&job.device_selector)?;
        let stream_selector = selector_from_value(&job.stream_selector)?;
        let devices = device_selector.first_devices();
        let streams = stream_selector.first_streams();
        if devices.is_empty() || streams.is_empty() {
            summary.skipped_jobs += 1;
            warn!(
                job_id = job.job_id,
                "job has no explicit devices or streams"
            );
            continue;
        }

        let threshold: ThresholdConfig = serde_json::from_value(job.detector_config.clone())?;
        for device_id in &devices {
            for stream_key in &streams {
                let (window_start, window_end, points) = repo
                    .metric_window(device_id, stream_key, job.window_seconds)
                    .await?;
                if points.is_empty() {
                    continue;
                }
                summary.windows_scanned += 1;
                let result = evaluate_threshold(&points, &threshold);
                if !result.crossed {
                    repo.update_job_state_success(job.job_id, window_end)
                        .await?;
                    continue;
                }

                repo.upsert_detection_event(EventInsert {
                    job_id: job.job_id,
                    device_id: device_id.clone(),
                    stream_key: stream_key.clone(),
                    window_start,
                    window_end,
                    severity: job.severity.clone(),
                    score: result.score,
                    threshold: result.threshold,
                    details: json!({
                        "detector": "threshold",
                        "job_key": job.job_key,
                        "window_seconds": job.window_seconds,
                        "step_seconds": job.step_seconds,
                        "lookback_seconds": job.lookback_seconds,
                        "result": result.details,
                    }),
                })
                .await?;
                repo.update_job_state_success(job.job_id, window_end)
                    .await?;
                summary.events_created += 1;
            }
        }
    }

    Ok(summary)
}

pub async fn run_worker_loop(repo: Repository, poll_interval: Duration) -> anyhow::Result<()> {
    info!(
        interval_seconds = poll_interval.as_secs(),
        "starting anomaly worker loop"
    );
    loop {
        match run_detection_once(&repo, None).await {
            Ok(summary) => {
                info!(
                    jobs_scanned = summary.jobs_scanned,
                    windows_scanned = summary.windows_scanned,
                    events_created = summary.events_created,
                    skipped_jobs = summary.skipped_jobs,
                    "anomaly worker cycle completed"
                );
            }
            Err(exc) => error!(error = ?exc, "anomaly worker cycle failed"),
        }
        sleep(poll_interval).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn run_summary_defaults_to_zero() {
        let summary = RunSummary::default();
        assert_eq!(summary.jobs_scanned, 0);
        assert_eq!(summary.windows_scanned, 0);
        assert_eq!(summary.events_created, 0);
        assert_eq!(summary.skipped_jobs, 0);
    }
}
