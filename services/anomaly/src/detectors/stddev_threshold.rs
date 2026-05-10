use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use serde_json::json;

pub const DETECTOR_TYPE: &str = "stddev_threshold";

pub fn evaluate(points: &[MetricPoint], config: &DetectorConfig) -> DetectionResult {
    let score = crate::detectors::stddev(points);
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
    fn detects_variability_crossing() {
        let points = vec![
            MetricPoint {
                event_time: Utc::now(),
                value: 0.0,
            },
            MetricPoint {
                event_time: Utc::now(),
                value: 10.0,
            },
        ];
        assert!(
            evaluate(
                &points,
                &DetectorConfig {
                    threshold: 4.0,
                    ..DetectorConfig::default()
                }
            )
            .crossed
        );
    }
}
