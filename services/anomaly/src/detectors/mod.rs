pub mod delta_threshold;
pub mod duty_cycle;
pub mod event_sequence;
pub mod ewma_deviation;
pub mod fft_threshold;
pub mod flatline;
pub mod mean_threshold;
pub mod missing_data;
pub mod peak_abs_threshold;
pub mod range;
pub mod rate_of_change;
pub mod rms_threshold;
pub mod stddev_threshold;
pub mod stuck_at;
pub mod threshold;
pub mod zscore_threshold;

use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use anyhow::anyhow;
use serde_json::json;

pub const DETECTOR_VERSION: &str = "1.0.0";

pub fn evaluate(
    detector_type: &str,
    points: &[MetricPoint],
    config: &DetectorConfig,
) -> anyhow::Result<DetectionResult> {
    match detector_type {
        threshold::DETECTOR_TYPE => Ok(threshold::evaluate(points, config)),
        range::DETECTOR_TYPE => Ok(range::evaluate(points, config)),
        mean_threshold::DETECTOR_TYPE => Ok(mean_threshold::evaluate(points, config)),
        rms_threshold::DETECTOR_TYPE => Ok(rms_threshold::evaluate(points, config)),
        peak_abs_threshold::DETECTOR_TYPE => Ok(peak_abs_threshold::evaluate(points, config)),
        stddev_threshold::DETECTOR_TYPE => Ok(stddev_threshold::evaluate(points, config)),
        delta_threshold::DETECTOR_TYPE => Ok(delta_threshold::evaluate(points, config)),
        missing_data::DETECTOR_TYPE => Ok(missing_data::evaluate(points, config)),
        flatline::DETECTOR_TYPE => Ok(flatline::evaluate(points, config)),
        rate_of_change::DETECTOR_TYPE => Ok(rate_of_change::evaluate(points, config)),
        zscore_threshold::DETECTOR_TYPE => Ok(zscore_threshold::evaluate(points, config)),
        ewma_deviation::DETECTOR_TYPE => Ok(ewma_deviation::evaluate(points, config)),
        stuck_at::DETECTOR_TYPE => Ok(stuck_at::evaluate(points, config)),
        duty_cycle::DETECTOR_TYPE => Ok(duty_cycle::evaluate(points, config)),
        event_sequence::DETECTOR_TYPE => Ok(event_sequence::evaluate(points, config)),
        fft_threshold::DETECTOR_TYPE => Ok(fft_threshold::evaluate(points, config)),
        _ => Err(anyhow!("unsupported detector type: {detector_type}")),
    }
}

pub fn is_supported(detector_type: &str) -> bool {
    matches!(
        detector_type,
        threshold::DETECTOR_TYPE
            | range::DETECTOR_TYPE
            | mean_threshold::DETECTOR_TYPE
            | rms_threshold::DETECTOR_TYPE
            | peak_abs_threshold::DETECTOR_TYPE
            | stddev_threshold::DETECTOR_TYPE
            | delta_threshold::DETECTOR_TYPE
            | missing_data::DETECTOR_TYPE
            | flatline::DETECTOR_TYPE
            | rate_of_change::DETECTOR_TYPE
            | zscore_threshold::DETECTOR_TYPE
            | ewma_deviation::DETECTOR_TYPE
            | stuck_at::DETECTOR_TYPE
            | duty_cycle::DETECTOR_TYPE
            | event_sequence::DETECTOR_TYPE
            | fft_threshold::DETECTOR_TYPE
    )
}

pub fn values(points: &[MetricPoint]) -> impl Iterator<Item = f64> + '_ {
    points.iter().map(|point| point.value)
}

pub fn max_value(points: &[MetricPoint]) -> f64 {
    values(points).fold(f64::NEG_INFINITY, f64::max)
}

pub fn min_value(points: &[MetricPoint]) -> f64 {
    values(points).fold(f64::INFINITY, f64::min)
}

pub fn mean(points: &[MetricPoint]) -> f64 {
    if points.is_empty() {
        0.0
    } else {
        values(points).sum::<f64>() / points.len() as f64
    }
}

pub fn rms(points: &[MetricPoint]) -> f64 {
    if points.is_empty() {
        0.0
    } else {
        (values(points).map(|value| value * value).sum::<f64>() / points.len() as f64).sqrt()
    }
}

pub fn stddev(points: &[MetricPoint]) -> f64 {
    if points.is_empty() {
        0.0
    } else {
        let avg = mean(points);
        (values(points)
            .map(|value| {
                let diff = value - avg;
                diff * diff
            })
            .sum::<f64>()
            / points.len() as f64)
            .sqrt()
    }
}

pub fn peak_abs(points: &[MetricPoint]) -> f64 {
    values(points).map(f64::abs).fold(0.0, f64::max)
}

pub fn elapsed_seconds(points: &[MetricPoint]) -> f64 {
    match (points.first(), points.last()) {
        (Some(first), Some(last)) => {
            let micros = (last.event_time - first.event_time)
                .num_microseconds()
                .unwrap_or_default();
            (micros as f64 / 1_000_000.0).max(0.0)
        }
        _ => 0.0,
    }
}

pub fn nominal_sample_rate_hz(points: &[MetricPoint]) -> Option<f64> {
    if points.len() < 2 {
        return None;
    }
    let elapsed = elapsed_seconds(points);
    if elapsed <= 0.0 {
        None
    } else {
        Some((points.len() - 1) as f64 / elapsed)
    }
}

pub fn compare(score: f64, threshold: f64, operator: &str) -> bool {
    match operator {
        "gte" => score >= threshold,
        "lt" => score < threshold,
        "lte" => score <= threshold,
        _ => score > threshold,
    }
}

pub fn result(
    detector_type: &str,
    crossed: bool,
    score: f64,
    threshold: f64,
    points: &[MetricPoint],
    extra: serde_json::Value,
) -> DetectionResult {
    DetectionResult {
        crossed,
        score: if score.is_finite() { score } else { 0.0 },
        threshold,
        details: json!({
            "detector": detector_type,
            "point_count": points.len(),
            "first_point_time": points.first().map(|point| point.event_time),
            "last_point_time": points.last().map(|point| point.event_time),
            "extra": extra,
        }),
    }
}
