use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use serde_json::json;

pub const DETECTOR_TYPE: &str = "duty_cycle";

pub fn evaluate(points: &[MetricPoint], config: &DetectorConfig) -> DetectionResult {
    let on_threshold = config.baseline.unwrap_or(0.5);
    let score = if points.is_empty() {
        0.0
    } else {
        points
            .iter()
            .filter(|point| point.value > on_threshold)
            .count() as f64
            / points.len() as f64
    };
    crate::detectors::result(
        DETECTOR_TYPE,
        crate::detectors::compare(score, config.threshold, &config.operator),
        score,
        config.threshold,
        points,
        json!({ "on_threshold": on_threshold, "operator": config.operator }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;

    #[test]
    fn detects_high_duty_cycle() {
        let points = vec![
            MetricPoint {
                event_time: Utc::now(),
                value: 1.0,
            },
            MetricPoint {
                event_time: Utc::now(),
                value: 1.0,
            },
            MetricPoint {
                event_time: Utc::now(),
                value: 0.0,
            },
        ];
        assert!(
            evaluate(
                &points,
                &DetectorConfig {
                    threshold: 0.6,
                    ..DetectorConfig::default()
                }
            )
            .crossed
        );
    }
}
