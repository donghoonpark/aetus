use crate::detectors;
use crate::models::{DetectorConfig, NumericWindow};
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

        if !detectors::is_supported(&job.detector_type) {
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

        let detector_config: DetectorConfig = serde_json::from_value(job.detector_config.clone())?;
        for device_id in &devices {
            for stream_key in &streams {
                let windows = if let Some(anchor) = &detector_config.anchor {
                    let anchor_times = repo.anchor_event_times(device_id, anchor).await?;
                    let mut anchored_windows = Vec::new();
                    for anchor_time in anchor_times {
                        let window_start =
                            anchor_time - chrono::Duration::seconds(anchor.pre_seconds as i64);
                        let window_end =
                            anchor_time + chrono::Duration::seconds(anchor.post_seconds as i64);
                        anchored_windows.extend(
                            repo.numeric_windows_between(
                                device_id,
                                stream_key,
                                &stream_selector.channels,
                                window_start,
                                window_end,
                                detector_config.max_points,
                            )
                            .await?,
                        );
                    }
                    anchored_windows
                } else {
                    repo.numeric_windows(
                        device_id,
                        stream_key,
                        &stream_selector.channels,
                        job.window_seconds,
                        detector_config.max_points,
                    )
                    .await?
                };

                let windows = if windows.is_empty()
                    && job.detector_type == detectors::missing_data::DETECTOR_TYPE
                {
                    let now = chrono::Utc::now();
                    vec![NumericWindow {
                        source_kind: "missing".to_string(),
                        channel_key: None,
                        window_start: now - chrono::Duration::seconds(job.window_seconds as i64),
                        window_end: now,
                        points: Vec::new(),
                        truncated: false,
                    }]
                } else {
                    windows
                };

                for window in windows {
                    summary.windows_scanned += 1;
                    let result =
                        detectors::evaluate(&job.detector_type, &window.points, &detector_config)?;
                    if !result.crossed {
                        repo.update_job_state_success(job.job_id, window.window_end)
                            .await?;
                        continue;
                    }

                    repo.upsert_detection_event(EventInsert {
                        job_id: job.job_id,
                        device_id: device_id.clone(),
                        stream_key: stream_key.clone(),
                        channel_key: window.channel_key.clone(),
                        window_start: window.window_start,
                        window_end: window.window_end,
                        severity: job.severity.clone(),
                        score: result.score,
                        threshold: result.threshold,
                        detector_type: job.detector_type.clone(),
                        detector_version: detectors::DETECTOR_VERSION.to_string(),
                        details: json!({
                            "detector": job.detector_type,
                            "job_key": job.job_key,
                            "window_seconds": job.window_seconds,
                            "step_seconds": job.step_seconds,
                            "lookback_seconds": job.lookback_seconds,
                            "source_kind": window.source_kind,
                            "channel_key": window.channel_key,
                            "truncated": window.truncated,
                            "anchor": detector_config.anchor,
                            "result": result.details,
                        }),
                    })
                    .await?;
                    repo.update_job_state_success(job.job_id, window.window_end)
                        .await?;
                    summary.events_created += 1;
                }
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
