from __future__ import annotations

import ipaddress

from aetus_ingest.control_db import ControlDB
from aetus_ingest.config import Settings


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


async def verify_device_token(device_id: str, token: str, control_db: ControlDB) -> bool:
    expected = await control_db.get_device_token_readonly(device_id)
    return expected is not None and expected == token
