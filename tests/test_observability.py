import logging

from convictionos.observability import Redactor, configure_json_logging, metric_line


def test_redactor_masks_secret_keys_and_configured_secret_values() -> None:
    redactor = Redactor(secret_values=("actual-secret", "control-token"))

    cleaned = redactor.clean(
        {
            "authorization": "Bearer actual-secret",
            "nested": {
                "api_key": "actual-secret",
                "safe": "paper-only",
                "items": ["prefix-control-token-suffix"],
            },
        }
    )

    rendered = str(cleaned)
    assert "actual-secret" not in rendered
    assert "control-token" not in rendered
    assert cleaned["authorization"] == "[REDACTED]"
    assert cleaned["nested"]["safe"] == "paper-only"


def test_configured_json_logging_redacts_canary_secret(caplog) -> None:
    logger = configure_json_logging(
        release_sha="f" * 40,
        secret_values=("canary-secret",),
    )

    with caplog.at_level(logging.INFO):
        logger.info("startup %s", {"token": "canary-secret", "role": "api"})

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "canary-secret" not in rendered
    assert "[REDACTED]" in rendered
    assert "ffffffffffffffffffffffffffffffffffffffff" in rendered


def test_metric_line_rejects_high_cardinality_private_labels() -> None:
    assert (
        metric_line("convictionos_release_info", 1, {"release_sha": "f" * 40})
        == 'convictionos_release_info{release_sha="ffffffffffffffffffffffffffffffffffffffff"} 1'
    )
    blocked = metric_line(
        "convictionos_broker_orders_total",
        1,
        {"client_order_id": "intent-001-v1"},
    )
    assert blocked == "# metric omitted: private label"
