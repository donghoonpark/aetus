use crate::models::{DetectionResult, MetricPoint, ThresholdConfig};
use serde_json::json;

pub const THRESHOLD_DETECTOR_VERSION: &str = "1.0.0";

pub fn evaluate_threshold(points: &[MetricPoint], config: &ThresholdConfig) -> DetectionResult {
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
    DetectionResult {
        crossed,
        score: if score.is_finite() { score } else { 0.0 },
        threshold: config.threshold,
        details: json!({
            "operator": config.operator,
            "point_count": points.len(),
            "first_point_time": points.first().map(|point| point.event_time),
            "last_point_time": points.last().map(|point| point.event_time),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;

    #[test]
    fn threshold_crosses_when_max_exceeds_limit() {
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

        let result = evaluate_threshold(
            &points,
            &ThresholdConfig {
                threshold: 50.0,
                operator: "gt".to_string(),
            },
        );

        assert!(result.crossed);
        assert_eq!(result.score, 55.0);
    }

    #[test]
    fn threshold_does_not_cross_when_values_stay_below_limit() {
        let points = vec![MetricPoint {
            event_time: Utc::now(),
            value: 49.0,
        }];

        let result = evaluate_threshold(
            &points,
            &ThresholdConfig {
                threshold: 50.0,
                operator: "gt".to_string(),
            },
        );

        assert!(!result.crossed);
    }

    #[test]
    fn lower_bound_threshold_uses_minimum_score() {
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

        let result = evaluate_threshold(
            &points,
            &ThresholdConfig {
                threshold: 5.0,
                operator: "lt".to_string(),
            },
        );

        assert!(result.crossed);
        assert_eq!(result.score, 4.0);
    }
}
