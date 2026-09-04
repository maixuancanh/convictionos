import json

from convictionos.infrastructure.webhook_notifications import SignedWebhookPayload


def test_signed_webhook_payload_redacts_private_fields_and_verifies_signature() -> None:
    payload = SignedWebhookPayload.create(
        secret="webhook-secret",
        event={
            "public_id": "inc_123",
            "kind": "broker_reconciliation",
            "account_id": "paper-account",
            "token": "control-secret",
        },
    )

    rendered = json.dumps(payload.body, sort_keys=True)
    assert "paper-account" not in rendered
    assert "control-secret" not in rendered
    assert payload.headers["X-ConvictionOS-Signature"].startswith("sha256=")
    assert payload.verify("webhook-secret") is True
    assert payload.verify("wrong-secret") is False
