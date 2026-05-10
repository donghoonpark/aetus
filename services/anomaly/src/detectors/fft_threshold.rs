use crate::models::{DetectionResult, DetectorConfig, MetricPoint};
use serde_json::json;
use std::f64::consts::TAU;

pub const DETECTOR_TYPE: &str = "fft_threshold";

pub fn evaluate(points: &[MetricPoint], config: &DetectorConfig) -> DetectionResult {
    let sampled = downsample(points, config.fft_sample_limit.max(8));
    let sample_rate_hz = crate::detectors::nominal_sample_rate_hz(&sampled).unwrap_or(0.0);
    let values = sampled.iter().map(|point| point.value).collect::<Vec<_>>();
    let mean = if values.is_empty() {
        0.0
    } else {
        values.iter().sum::<f64>() / values.len() as f64
    };
    let centered = values.iter().map(|value| value - mean).collect::<Vec<_>>();
    let (score, frequency_hz) = if let Some(target) = config.target_frequency_hz {
        let bin = target_bin(target, sample_rate_hz, centered.len());
        (
            bin_magnitude(&centered, bin),
            bin_frequency(bin, sample_rate_hz, centered.len()),
        )
    } else {
        dominant_bin(&centered, sample_rate_hz)
    };
    crate::detectors::result(
        DETECTOR_TYPE,
        crate::detectors::compare(score, config.threshold, &config.operator),
        score,
        config.threshold,
        &sampled,
        json!({
            "operator": config.operator,
            "sample_rate_hz": sample_rate_hz,
            "frequency_hz": frequency_hz,
            "target_frequency_hz": config.target_frequency_hz,
            "fft_sample_limit": config.fft_sample_limit,
        }),
    )
}

fn downsample(points: &[MetricPoint], limit: usize) -> Vec<MetricPoint> {
    if points.len() <= limit {
        return points.to_vec();
    }
    (0..limit)
        .map(|index| {
            let source_index = index * (points.len() - 1) / (limit - 1);
            points[source_index].clone()
        })
        .collect()
}

fn dominant_bin(values: &[f64], sample_rate_hz: f64) -> (f64, f64) {
    if values.len() < 2 {
        return (0.0, 0.0);
    }
    let mut best = (0.0, 0.0);
    for bin in 1..=(values.len() / 2) {
        let magnitude = bin_magnitude(values, bin);
        if magnitude > best.0 {
            best = (magnitude, bin_frequency(bin, sample_rate_hz, values.len()));
        }
    }
    best
}

fn target_bin(target_hz: f64, sample_rate_hz: f64, len: usize) -> usize {
    if sample_rate_hz <= 0.0 || len < 2 {
        return 0;
    }
    ((target_hz * len as f64 / sample_rate_hz).round() as usize).clamp(1, len / 2)
}

fn bin_frequency(bin: usize, sample_rate_hz: f64, len: usize) -> f64 {
    if len == 0 {
        0.0
    } else {
        bin as f64 * sample_rate_hz / len as f64
    }
}

fn bin_magnitude(values: &[f64], bin: usize) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let len = values.len() as f64;
    let mut real = 0.0;
    let mut imag = 0.0;
    for (index, value) in values.iter().enumerate() {
        let phase = TAU * bin as f64 * index as f64 / len;
        real += value * phase.cos();
        imag -= value * phase.sin();
    }
    (real.mul_add(real, imag * imag)).sqrt() * 2.0 / len
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{Duration, Utc};

    #[test]
    fn detects_target_frequency_magnitude() {
        let start = Utc::now();
        let points = (0..64)
            .map(|index| {
                let value = (TAU * 4.0 * index as f64 / 64.0).sin();
                MetricPoint {
                    event_time: start + Duration::milliseconds(index as i64 * 10),
                    value,
                }
            })
            .collect::<Vec<_>>();
        let result = evaluate(
            &points,
            &DetectorConfig {
                target_frequency_hz: Some(6.25),
                threshold: 0.8,
                ..DetectorConfig::default()
            },
        );
        assert!(result.crossed);
        assert!(result.score > 0.8);
    }
}
