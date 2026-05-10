use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use serde_json::json;

pub const DETECTOR_TYPE: &str = "rate_of_change";

pub fn evaluate(points: &[MetricPoint], config: &DetectorConfig) -> DetectionResult {
    let score = match (points.first(), points.last()) {
        (Some(first), Some(last)) => {
            let elapsed = crate::detectors::elapsed_seconds(points);
            if elapsed > 0.0 {
                (last.value - first.value).abs() / elapsed
            } else {
                0.0
            }
        }
        _ => 0.0,
    };
    crate::detectors::result(
        DETECTOR_TYPE,
        crate::detectors::compare(score, config.threshold, &config.operator),
        score,
        config.threshold,
        points,
        json!({ "operator": config.operator, "unit": "value_per_second" }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{Duration, Utc};

    #[test]
    fn detects_fast_rate_of_change() {
        let start = Utc::now();
        let points = vec![
            MetricPoint {
                event_time: start,
                value: 0.0,
            },
            MetricPoint {
                event_time: start + Duration::seconds(2),
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
