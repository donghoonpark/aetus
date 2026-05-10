use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use serde_json::json;

pub const DETECTOR_TYPE: &str = "missing_data";

pub fn evaluate(points: &[MetricPoint], config: &DetectorConfig) -> DetectionResult {
    let min_count = config.min_count.unwrap_or(1);
    let score = points.len() as f64;
    crate::detectors::result(
        DETECTOR_TYPE,
        points.len() < min_count,
        score,
        min_count as f64,
        points,
        json!({ "min_count": min_count }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_missing_data() {
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
