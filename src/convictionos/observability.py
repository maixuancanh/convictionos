from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

PRIVATE_LABEL_KEYS = {
    "account_id",
    "authorization",
    "broker_order_id",
    "client_order_id",
    "cookie",
    "idempotency_key",
    "password",
    "secret",
    "token",
}


class Redactor:
    def __init__(self, *, secret_values: tuple[str, ...] = ()) -> None:
        self._secret_values = tuple(value for value in secret_values if value)

    def clean(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: "[REDACTED]" if _is_secret_key(str(key)) else self.clean(item)
                for key, item in value.items()
            }
        if isinstance(value, tuple):
            return tuple(self.clean(item) for item in value)
        if isinstance(value, list):
            return [self.clean(item) for item in value]
        if isinstance(value, str):
            cleaned = value
            for secret in self._secret_values:
                cleaned = cleaned.replace(secret, "[REDACTED]")
            return cleaned
        return value


class RedactingJsonFormatter(logging.Formatter):
    def __init__(self, *, release_sha: str, redactor: Redactor) -> None:
        super().__init__()
        self._release_sha = release_sha
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload = {
            "severity": record.levelname,
            "logger": record.name,
            "release_sha": self._release_sha,
            "message": self._redactor.clean(message),
        }
        return json.dumps(payload, sort_keys=True)


class RedactingFilter(logging.Filter):
    def __init__(self, redactor: Redactor, *, release_sha: str) -> None:
        super().__init__()
        self._redactor = redactor
        self._release_sha = release_sha

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = self._redactor.clean(f"{message} release_sha={self._release_sha}")
        record.args = ()
        return True


def configure_json_logging(
    *,
    release_sha: str,
    secret_values: tuple[str, ...] = (),
    logger_name: str = "convictionos",
) -> logging.Logger:
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = True
    redactor = Redactor(secret_values=secret_values)
    formatter = RedactingJsonFormatter(release_sha=release_sha, redactor=redactor)
    logger.addFilter(RedactingFilter(redactor, release_sha=release_sha))
    for handler in logger.handlers:
        handler.setFormatter(formatter)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def metric_line(name: str, value: int | float, labels: Mapping[str, str] | None = None) -> str:
    labels = labels or {}
    if any(_is_secret_key(key) for key in labels):
        return "# metric omitted: private label"
    if not labels:
        return f"{name} {value}"
    rendered_labels = ",".join(
        f'{key}="{_escape_label(label_value)}"'
        for key, label_value in sorted(labels.items())
    )
    return f"{name}{{{rendered_labels}}} {value}"


def _is_secret_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in PRIVATE_LABEL_KEYS)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
