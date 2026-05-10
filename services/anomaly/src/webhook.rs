use hmac::{Hmac, Mac};
use sha2::Sha256;
use std::time::{SystemTime, UNIX_EPOCH};

type HmacSha256 = Hmac<Sha256>;

pub fn now_unix_seconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or_default()
}

pub fn sign_payload(secret: &str, timestamp: i64, body: &[u8]) -> anyhow::Result<String> {
    let mut signing_input = timestamp.to_string().into_bytes();
    signing_input.push(b'.');
    signing_input.extend_from_slice(body);
    let mut mac = HmacSha256::new_from_slice(secret.as_bytes())?;
    mac.update(&signing_input);
    Ok(format!(
        "hmac-sha256={}",
        hex::encode(mac.finalize().into_bytes())
    ))
}

pub fn next_backoff_seconds(attempt_count: i32) -> i64 {
    let bounded = attempt_count.clamp(0, 8);
    2_i64.pow(bounded as u32).min(300)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signing_is_stable_for_same_payload() {
        let first = sign_payload("secret", 123, br#"{"ok":true}"#).unwrap();
        let second = sign_payload("secret", 123, br#"{"ok":true}"#).unwrap();
        assert_eq!(first, second);
        assert!(first.starts_with("hmac-sha256="));
    }

    #[test]
    fn backoff_is_bounded() {
        assert_eq!(next_backoff_seconds(0), 1);
        assert_eq!(next_backoff_seconds(3), 8);
        assert_eq!(next_backoff_seconds(20), 256);
    }
}
