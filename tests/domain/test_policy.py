from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from convictionos.domain.mandates import (
    ApprovalMode,
    KillSwitch,
    Mandate,
    PolicyContext,
    PolicyOutcome,
    evaluate_policy,
)
from tests.domain.test_trade_intent import make_intent

NOW = datetime(2026, 8, 29, 14, 1, tzinfo=UTC)


def mandate() -> Mandate:
    return Mandate(
        mandate_id="mandate-001",
        version=1,
        account_id="paper-account",
        expires_at=NOW + timedelta(days=30),
        approval_mode=ApprovalMode.GUARDED,
        allowed_underlyings=frozenset({"SPY"}),
        max_trade_loss=Decimal("250"),
        max_notional=Decimal("1000"),
        autonomous_notional=Decimal("200"),
        required_options_level=3,
    )


def context(**changes: object) -> PolicyContext:
    values: dict[str, object] = {
        "now": NOW,
        "data_fresh": True,
        "kill_switch": KillSwitch.OFF,
        "broker_options_level": 3,
        "projected_notional": Decimal("125"),
        "projected_max_loss": Decimal("125"),
    }
    values.update(changes)
    return PolicyContext(**values)


def test_allows_intent_inside_autonomous_threshold() -> None:
    assert evaluate_policy(make_intent(), mandate(), context()).outcome is PolicyOutcome.ALLOW


def test_escalates_above_autonomous_threshold() -> None:
    decision = evaluate_policy(make_intent(), mandate(), context(projected_notional=Decimal("300")))
    assert decision.outcome is PolicyOutcome.ESCALATE
    assert decision.reasons == ("autonomous_notional_exceeded",)


def test_stale_data_is_hard_denied() -> None:
    decision = evaluate_policy(make_intent(), mandate(), context(data_fresh=False))
    assert decision.outcome is PolicyOutcome.DENY
    assert "stale_critical_data" in decision.reasons


def test_user_cannot_override_broker_options_level() -> None:
    decision = evaluate_policy(make_intent(), mandate(), context(broker_options_level=2))
    assert decision.outcome is PolicyOutcome.DENY
    assert "insufficient_broker_options_level" in decision.reasons


def test_mandate_rejects_naive_expires_at() -> None:
    with pytest.raises(ValidationError, match="expires_at"):
        Mandate(
            mandate_id="mandate-001",
            version=1,
            account_id="paper-account",
            expires_at=datetime(2026, 8, 29, 14, 1),
            approval_mode=ApprovalMode.GUARDED,
            allowed_underlyings=frozenset({"SPY"}),
            max_trade_loss=Decimal("250"),
            max_notional=Decimal("1000"),
            autonomous_notional=Decimal("200"),
            required_options_level=3,
        )


def test_policy_context_rejects_naive_now() -> None:
    with pytest.raises(ValidationError, match="now"):
        PolicyContext(
            now=datetime(2026, 8, 29, 14, 1),
            data_fresh=True,
            kill_switch=KillSwitch.OFF,
            broker_options_level=3,
            projected_notional=Decimal("125"),
            projected_max_loss=Decimal("125"),
        )


def test_hard_deny_wins_with_deterministic_reasons() -> None:
    decision = evaluate_policy(
        make_intent(),
        mandate(),
        context(
            data_fresh=False,
            broker_options_level=2,
            projected_notional=Decimal("1500"),
            projected_max_loss=Decimal("500"),
        ),
    )
    assert decision.outcome is PolicyOutcome.DENY
    assert decision.reasons == (
        "stale_critical_data",
        "insufficient_broker_options_level",
        "max_trade_loss_exceeded",
        "max_notional_exceeded",
    )
