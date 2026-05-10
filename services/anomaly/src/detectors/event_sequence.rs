use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use serde_json::json;

pub const DETECTOR_TYPE: &str = "event_sequence";

pub fn evaluate(points: &[MetricPoint], config: &DetectorConfig) -> DetectionResult {
    let min_count = config.min_count.unwrap_or(1);
    let max_count = config.max_count.unwrap_or(usize::MAX);
    let count = points.len();
    let crossed = count < min_count || count > max_count;
    let threshold = if count < min_count {
        min_count as f64
    } else {
        max_count as f64
    };
    crate::detectors::result(
        DETECTOR_TYPE,
        crossed,
        count as f64,
        threshold,
        points,
        json!({ "min_count": min_count, "max_count": max_count }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_missing_required_event_count() {
        assert!(
            evaluate(
                &[],
                &DetectorConfig {
                    min_count: Some(1),
                    ..DetectorConfig::default()
                }
            )
            .crossed
        );
    }
}
