use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use serde_json::json;

pub const DETECTOR_TYPE: &str = "range";

pub fn evaluate(points: &[MetricPoint], config: &DetectorConfig) -> DetectionResult {
    let min_allowed = config.min.unwrap_or(f64::NEG_INFINITY);
    let max_allowed = config.max.unwrap_or(f64::INFINITY);
    let min_seen = crate::detectors::min_value(points);
    let max_seen = crate::detectors::max_value(points);
    let low_distance = if min_seen < min_allowed {
        min_allowed - min_seen
    } else {
        0.0
    };
    let high_distance = if max_seen > max_allowed {
        max_seen - max_allowed
    } else {
        0.0
    };
    let score = low_distance.max(high_distance);
    crate::detectors::result(
        DETECTOR_TYPE,
        score > 0.0,
        score,
        0.0,
        points,
        json!({ "min_allowed": config.min, "max_allowed": config.max, "min_seen": min_seen, "max_seen": max_seen }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::MetricPoint;
    use chrono::Utc;

    #[test]
    fn detects_out_of_range_values() {
        let points = vec![MetricPoint {
            event_time: Utc::now(),
            value: 12.0,
        }];
        let result = evaluate(
            &points,
            &DetectorConfig {
                min: Some(0.0),
                max: Some(10.0),
                ..DetectorConfig::default()
            },
        );
        assert!(result.crossed);
        assert_eq!(result.score, 2.0);
    }
}
