use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use serde_json::json;

pub const DETECTOR_TYPE: &str = "mean_threshold";

pub fn evaluate(points: &[MetricPoint], config: &DetectorConfig) -> DetectionResult {
    let score = crate::detectors::mean(points);
    crate::detectors::result(
        DETECTOR_TYPE,
        crate::detectors::compare(score, config.threshold, &config.operator),
        score,
        config.threshold,
        points,
        json!({ "operator": config.operator }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::MetricPoint;
    use chrono::Utc;

    #[test]
    fn detects_mean_crossing() {
        let points = vec![
            MetricPoint {
                event_time: Utc::now(),
                value: 10.0,
            },
            MetricPoint {
                event_time: Utc::now(),
                value: 20.0,
            },
        ];
        assert!(
            evaluate(
                &points,
                &DetectorConfig {
                    threshold: 14.0,
                    ..DetectorConfig::default()
                }
            )
            .crossed
        );
    }
}
