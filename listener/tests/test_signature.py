"""Unit tests for HMAC signature verification."""

from __future__ import annotations

import hashlib
import hmac

import pytest
from webhook_listener.signature import SignatureError, verify_hmac_sha256

SHARED = b"shh-secret"


def _sign(body: bytes, key: bytes = SHARED) -> str:
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def test_accepts_valid_signature():
    body = b'{"hello":"world"}'
    assert verify_hmac_sha256(body, _sign(body), SHARED) is True


def test_accepts_prefixed_header():
    body = b"payload"
    assert verify_hmac_sha256(body, "sha256=" + _sign(body), SHARED) is True


def test_rejects_tampered_body():
    body = b"original"
    sig = _sign(body)
    with pytest.raises(SignatureError):
        verify_hmac_sha256(b"tampered", sig, SHARED)


def test_rejects_missing_header():
    with pytest.raises(SignatureError):
        verify_hmac_sha256(b"body", None, SHARED)


def test_rejects_empty_header():
    with pytest.raises(SignatureError):
        verify_hmac_sha256(b"body", "", SHARED)


def test_accepts_str_shared_key():
    body = b"str-key"
    assert verify_hmac_sha256(body, _sign(body), "shh-secret") is True
