use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use serde_json::json;

pub const DETECTOR_TYPE: &str = "delta_threshold";

pub fn evaluate(points: &[MetricPoint], config: &DetectorConfig) -> DetectionResult {
    let score = match (points.first(), points.last()) {
        (Some(first), Some(last)) => (last.value - first.value).abs(),
        _ => 0.0,
    };
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
    fn detects_large_first_to_last_delta() {
        let points = vec![
            MetricPoint {
                event_time: Utc::now(),
                value: 1.0,
            },
            MetricPoint {
                event_time: Utc::now(),
                value: 9.0,
            },
        ];
        assert!(
            evaluate(
                &points,
                &DetectorConfig {
                    threshold: 7.0,
                    ..DetectorConfig::default()
                }
            )
            .crossed
        );
    }
}
