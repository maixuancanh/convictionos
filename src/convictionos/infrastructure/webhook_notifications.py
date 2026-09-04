from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from convictionos.observability import Redactor


@dataclass(frozen=True)
class SignedWebhookPayload:
    body: dict[str, Any]
    headers: dict[str, str]

    @classmethod
    def create(cls, *, secret: str, event: dict[str, Any]) -> SignedWebhookPayload:
        body = Redactor(secret_values=()).clean(event)
        body = {
            key: value
            for key, value in body.items()
            if key not in {"account_id", "broker_order_id", "client_order_id"}
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(secret.encode(), encoded, sha256).hexdigest()
        return cls(
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-ConvictionOS-Signature": f"sha256={signature}",
            },
        )

    def verify(self, secret: str) -> bool:
        expected = self.create(secret=secret, event=self.body)
        return hmac.compare_digest(
            self.headers["X-ConvictionOS-Signature"],
            expected.headers["X-ConvictionOS-Signature"],
        )
