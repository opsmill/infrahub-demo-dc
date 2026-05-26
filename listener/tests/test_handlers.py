"""Unit tests for the webhook listener handler.

Covers schema validation, signature verification, dedup, error-body hygiene
(no tracebacks), and the happy-path acceptance response.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures" / "vyos"
SHARED_KEY = b"test-shared-key"


def _valid_body(**overrides) -> dict:
    base = {
        "schema_version": "2",
        "event_id": "01HXYZABC1234567890",
        "occurred_at": "2026-04-16T14:30:22.581Z",
        "branch": "main",
        "hfid": ["fw1"],
        "device_key": "fw1",
        "device_kind": "DcimFirewall",
        "device_node_id": "0190a8f1-1111-7777-aaaa-333344445555",
        "artifact": {
            "definition_name": "VyOSFirewallConfig",
            "artifact_name": "vyos-firewall-config",
            "url": "http://127.0.0.1:8010/api/artifact/0190a8f1-9999-7777-aaaa-111122223333",
            "storage_id": "0190a8f1-3333-7777-aaaa-777788889999",
            "checksum": "sha256:3b1c4f00112233445566778899aabbccddeeff00112233445566778899aabb",
            "checksum_previous": None,
        },
    }
    base.update(overrides)
    return base


def _sign(body: bytes) -> str:
    return hmac.new(SHARED_KEY, body, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WEBHOOK_LISTENER_SHARED_KEY", SHARED_KEY.decode())
    monkeypatch.setenv("WEBHOOK_LISTENER_DISABLE_SIG", "0")
    # Prevent the playbook subprocess from actually running in unit tests.
    monkeypatch.setenv("PATH", "/nonexistent")
    # Clear dedup cache between tests.
    from webhook_listener import handlers

    handlers._DEDUP_CACHE.clear()
    yield


@pytest.fixture
def client(monkeypatch):
    from webhook_listener.main import create_app

    return TestClient(create_app())


def test_happy_path_returns_200_with_request_id(client):
    body = json.dumps(_valid_body()).encode()
    resp = client.post(
        "/webhook",
        content=body,
        headers={
            "content-type": "application/json",
            "webhook-signature": _sign(body),
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["accepted"] is True
    assert "request_id" in data


def test_rejects_bad_signature(client):
    body = json.dumps(_valid_body()).encode()
    resp = client.post(
        "/webhook",
        content=body,
        headers={
            "content-type": "application/json",
            "webhook-signature": "deadbeef" * 8,
        },
    )
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}
    assert "Traceback" not in resp.text
    assert 'File "' not in resp.text


def test_missing_signature_header(client):
    body = json.dumps(_valid_body()).encode()
    resp = client.post("/webhook", content=body, headers={"content-type": "application/json"})
    assert resp.status_code == 401


def test_unsupported_schema_version_400(client):
    body = json.dumps(_valid_body(schema_version="1")).encode()
    resp = client.post(
        "/webhook",
        content=body,
        headers={
            "content-type": "application/json",
            "webhook-signature": _sign(body),
        },
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "schema_version" in detail
    assert "'1'" in detail
    assert "'2'" in detail


def test_missing_field_returns_400_with_detail(client):
    bad = _valid_body()
    del bad["hfid"]
    body = json.dumps(bad).encode()
    resp = client.post(
        "/webhook",
        content=body,
        headers={
            "content-type": "application/json",
            "webhook-signature": _sign(body),
        },
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "hfid" in detail


def test_deduplicates_on_event_id_returns_409(client):
    body = json.dumps(_valid_body()).encode()
    headers = {
        "content-type": "application/json",
        "webhook-signature": _sign(body),
    }
    first = client.post("/webhook", content=body, headers=headers)
    second = client.post("/webhook", content=body, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"] == "duplicate"
    assert second.json()["request_id"] == first.json()["request_id"]


def test_error_responses_never_contain_traceback(client):
    invalid = b"{ this is not valid json"
    resp = client.post(
        "/webhook",
        content=invalid,
        headers={
            "content-type": "application/json",
            "webhook-signature": _sign(invalid),
        },
    )
    assert resp.status_code == 400
    assert "Traceback" not in resp.text
    assert 'File "' not in resp.text
