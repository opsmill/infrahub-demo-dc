"""Verify Infrahub custom webhook signatures.

Infrahub follows the Standard Webhooks convention
(backend/infrahub/webhook/models.py):

  - `webhook-id: msg_<hex_uuid>`
  - `webhook-timestamp: <unix_seconds>`
  - `webhook-signature: v1,<base64(HMAC_SHA256(signed_content))>`
  - `signed_content = f"{webhook-id}.{webhook-timestamp}.{body_json}"` (utf-8)

Also accepts bare hex / `sha256=hex` over the raw body, for unit tests
that don't bother reconstructing Standard Webhooks headers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Mapping, Optional, Union


class SignatureError(Exception):
    """Raised when the webhook signature is missing or does not verify."""


def _decode_header(header: str) -> bytes:
    if not header:
        raise SignatureError("signature header missing")
    header = header.strip()
    if header.startswith("v1,"):
        return base64.b64decode(header.split(",", 1)[1])
    if header.startswith("sha256="):
        return bytes.fromhex(header.split("=", 1)[1])
    return bytes.fromhex(header)


def verify_hmac_sha256(
    body: bytes,
    signature_header: Union[str, None],
    shared_key: Union[bytes, str],
    headers: Optional[Mapping[str, str]] = None,
) -> bool:
    if signature_header is None:
        raise SignatureError("signature header missing")
    try:
        got = _decode_header(signature_header)
    except (ValueError, base64.binascii.Error) as exc:
        raise SignatureError(f"signature header malformed: {exc}")
    if isinstance(shared_key, str):
        shared_key = shared_key.encode("utf-8")

    if signature_header.startswith("v1,") and headers is not None:
        msg_id = headers.get("webhook-id", "")
        ts = headers.get("webhook-timestamp", "")
        if not msg_id or not ts:
            raise SignatureError("webhook-id/webhook-timestamp missing for v1 signature")
        signed_content = f"{msg_id}.{ts}.{body.decode('utf-8')}".encode("utf-8")
        expected = hmac.new(shared_key, signed_content, hashlib.sha256).digest()
    else:
        expected = hmac.new(shared_key, body, hashlib.sha256).digest()

    if not hmac.compare_digest(got, expected):
        raise SignatureError("signature mismatch")
    return True
