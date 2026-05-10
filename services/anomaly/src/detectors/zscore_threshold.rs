use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use serde_json::json;

pub const DETECTOR_TYPE: &str = "zscore_threshold";

pub fn evaluate(points: &[MetricPoint], config: &DetectorConfig) -> DetectionResult {
    let baseline = config
        .baseline
        .unwrap_or_else(|| crate::detectors::mean(points));
    let sigma = config
        .tolerance
        .unwrap_or_else(|| crate::detectors::stddev(points))
        .max(f64::EPSILON);
    let score = points
        .iter()
        .map(|point| ((point.value - baseline) / sigma).abs())
        .fold(0.0, f64::max);
    crate::detectors::result(
        DETECTOR_TYPE,
        crate::detectors::compare(score, config.threshold, &config.operator),
        score,
        config.threshold,
        points,
        json!({ "baseline": baseline, "sigma": sigma, "operator": config.operator }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;

    #[test]
    fn detects_zscore_outlier() {
        let points = vec![
            MetricPoint {
                event_time: Utc::now(),
                value: 10.0,
            },
            MetricPoint {
                event_time: Utc::now(),
                value: 16.0,
            },
        ];
        assert!(
            evaluate(
                &points,
                &DetectorConfig {
                    baseline: Some(10.0),
                    tolerance: Some(2.0),
                    threshold: 2.5,
                    ..DetectorConfig::default()
                }
            )
            .crossed
        );
    }
}
