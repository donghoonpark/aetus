use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use serde_json::json;

pub const DETECTOR_TYPE: &str = "threshold";

pub fn evaluate(points: &[MetricPoint], config: &DetectorConfig) -> DetectionResult {
    let uses_lower_bound = matches!(config.operator.as_str(), "lt" | "lte");
    let score = if uses_lower_bound {
        points
            .iter()
            .map(|point| point.value)
            .fold(f64::INFINITY, f64::min)
    } else {
        points
            .iter()
            .map(|point| point.value)
            .fold(f64::NEG_INFINITY, f64::max)
    };
    let crossed = match config.operator.as_str() {
        "gte" => score >= config.threshold,
        "lt" => score < config.threshold,
        "lte" => score <= config.threshold,
        _ => score > config.threshold,
    };
    crate::detectors::result(
        DETECTOR_TYPE,
        crossed,
        score,
        config.threshold,
        points,
        json!({
            "operator": config.operator,
        }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;

    #[test]
    fn crosses_when_max_exceeds_limit() {
        let points = vec![
            MetricPoint {
                event_time: Utc::now(),
                value: 20.0,
            },
            MetricPoint {
                event_time: Utc::now(),
                value: 55.0,
            },
        ];

        let result = evaluate(
            &points,
            &DetectorConfig {
                threshold: 50.0,
                operator: "gt".to_string(),
                ..DetectorConfig::default()
            },
        );

        assert!(result.crossed);
        assert_eq!(result.score, 55.0);
    }

    #[test]
    fn does_not_cross_when_values_stay_below_limit() {
        let points = vec![MetricPoint {
            event_time: Utc::now(),
            value: 49.0,
        }];

        let result = evaluate(
            &points,
            &DetectorConfig {
                threshold: 50.0,
                operator: "gt".to_string(),
                ..DetectorConfig::default()
            },
        );

        assert!(!result.crossed);
    }

    #[test]
    fn lower_bound_uses_minimum_score() {
        let points = vec![
            MetricPoint {
                event_time: Utc::now(),
                value: 12.0,
            },
            MetricPoint {
                event_time: Utc::now(),
                value: 4.0,
            },
        ];

        let result = evaluate(
            &points,
            &DetectorConfig {
                threshold: 5.0,
                operator: "lt".to_string(),
                ..DetectorConfig::default()
            },
        );

        assert!(result.crossed);
        assert_eq!(result.score, 4.0);
    }
}
