use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use serde_json::json;

pub const DETECTOR_TYPE: &str = "ewma_deviation";

pub fn evaluate(points: &[MetricPoint], config: &DetectorConfig) -> DetectionResult {
    let alpha = config.alpha.unwrap_or(0.2).clamp(0.001, 1.0);
    let mut ewma = config
        .baseline
        .or_else(|| points.first().map(|point| point.value))
        .unwrap_or_default();
    let mut score = 0.0;
    for point in points {
        ewma = alpha * point.value + (1.0 - alpha) * ewma;
        score = f64::max(score, (point.value - ewma).abs());
    }
    crate::detectors::result(
        DETECTOR_TYPE,
        crate::detectors::compare(score, config.threshold, &config.operator),
        score,
        config.threshold,
        points,
        json!({ "alpha": alpha, "final_ewma": ewma, "operator": config.operator }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;

    #[test]
    fn detects_ewma_deviation() {
        let points = vec![
            MetricPoint {
                event_time: Utc::now(),
                value: 10.0,
            },
            MetricPoint {
                event_time: Utc::now(),
                value: 30.0,
            },
        ];
        assert!(
            evaluate(
                &points,
                &DetectorConfig {
                    alpha: Some(0.1),
                    threshold: 15.0,
                    ..DetectorConfig::default()
                }
            )
            .crossed
        );
    }
}
