use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use serde_json::json;

pub const DETECTOR_TYPE: &str = "stuck_at";

pub fn evaluate(points: &[MetricPoint], config: &DetectorConfig) -> DetectionResult {
    let expected = config.expected_value.unwrap_or(config.threshold);
    let tolerance = config.tolerance.unwrap_or(0.0).abs();
    let min_count = config.min_count.unwrap_or(3);
    let stuck_count = points
        .iter()
        .filter(|point| (point.value - expected).abs() <= tolerance)
        .count();
    let score = stuck_count as f64;
    crate::detectors::result(
        DETECTOR_TYPE,
        stuck_count >= min_count,
        score,
        min_count as f64,
        points,
        json!({ "expected_value": expected, "tolerance": tolerance, "stuck_count": stuck_count, "min_count": min_count }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;

    #[test]
    fn detects_stuck_value() {
        let points = vec![
            MetricPoint {
                event_time: Utc::now(),
                value: 1023.0,
            },
            MetricPoint {
                event_time: Utc::now(),
                value: 1023.0,
            },
            MetricPoint {
                event_time: Utc::now(),
                value: 1022.9,
            },
        ];
        assert!(
            evaluate(
                &points,
                &DetectorConfig {
                    expected_value: Some(1023.0),
                    tolerance: Some(0.2),
                    min_count: Some(3),
                    ..DetectorConfig::default()
                }
            )
            .crossed
        );
    }
}
