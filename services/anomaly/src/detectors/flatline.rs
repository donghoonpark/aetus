use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use serde_json::json;

pub const DETECTOR_TYPE: &str = "flatline";

pub fn evaluate(points: &[MetricPoint], config: &DetectorConfig) -> DetectionResult {
    let min_count = config.min_count.unwrap_or(3);
    let min_seen = crate::detectors::min_value(points);
    let max_seen = crate::detectors::max_value(points);
    let range = if points.is_empty() {
        0.0
    } else {
        max_seen - min_seen
    };
    let threshold = config.threshold;
    crate::detectors::result(
        DETECTOR_TYPE,
        points.len() >= min_count && range <= threshold,
        range,
        threshold,
        points,
        json!({ "min_count": min_count, "min_seen": min_seen, "max_seen": max_seen }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::MetricPoint;
    use chrono::Utc;

    #[test]
    fn detects_flatline_when_range_is_tiny() {
        let points = vec![
            MetricPoint {
                event_time: Utc::now(),
                value: 1.0,
            },
            MetricPoint {
                event_time: Utc::now(),
                value: 1.01,
            },
            MetricPoint {
                event_time: Utc::now(),
                value: 1.02,
            },
        ];
        assert!(
            evaluate(
                &points,
                &DetectorConfig {
                    threshold: 0.05,
                    min_count: Some(3),
                    ..DetectorConfig::default()
                }
            )
            .crossed
        );
    }
}
