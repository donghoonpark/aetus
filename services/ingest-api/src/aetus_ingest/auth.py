from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re

from aetus_ingest.control_db import ControlStore
from aetus_ingest.config import Settings

HMAC_SIGNATURE_SCHEME = "hmac-sha256-v1"
HMAC_SIGNATURE_PREFIX = "AETUS-HMAC-SHA256-V1"
_HEX_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise ValueError("missing authorization")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ValueError("invalid authorization scheme")
    return token


def is_source_ip_allowed(source_ip: str, settings: Settings) -> bool:
    try:
        ip = ipaddress.ip_address(source_ip)
    except ValueError:
        return False

    return any(ip in network for network in settings.allowed_source_cidrs)


async def verify_device_token(device_id: str, token: str, control_db: ControlStore) -> bool:
    expected = await control_db.get_device_token_readonly(device_id)
    return expected is not None and expected == token


def parse_hmac_signature(signature_header: str | None) -> str:
    if not signature_header:
        raise ValueError("missing hmac signature")

    scheme, sep, signature_hex = signature_header.partition("=")
    if sep != "=" or scheme != HMAC_SIGNATURE_SCHEME or not _HEX_SHA256_RE.fullmatch(signature_hex):
        raise ValueError("invalid hmac signature")
    return signature_hex.lower()


def build_hmac_signing_input(*, method: str, path: str, device_id: str, body: bytes) -> bytes:
    body_sha256_hex = hashlib.sha256(body).hexdigest()
    prefix = f"{HMAC_SIGNATURE_PREFIX}\n{method.upper()}\n{path}\n{device_id}\n"
    return prefix.encode("utf-8") + body_sha256_hex.encode("ascii")


async def verify_hmac_signature(
    *,
    device_id: str,
    method: str,
    path: str,
    body: bytes,
    signature_header: str | None,
    control_db: ControlStore,
) -> bool:
    try:
        signature_hex = parse_hmac_signature(signature_header)
    except ValueError:
        return False

    secret = await control_db.get_device_token_readonly(device_id)
    if secret is None:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        build_hmac_signing_input(method=method, path=path, device_id=device_id, body=body),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_hex)
