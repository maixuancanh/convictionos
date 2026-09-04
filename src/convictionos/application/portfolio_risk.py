from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from convictionos.domain.intelligence import GroundedThesis, ThesisDirection
from convictionos.domain.portfolio_risk import (
    HorizonRiskBudget,
    PortfolioSnapshot,
)
from convictionos.domain.strategy import StrategyCandidate
from convictionos.domain.trading import Horizon


class RiskOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ABSTAIN = "abstain"


class RiskDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: RiskOutcome
    reasons: tuple[str, ...] = ()
    horizon: Horizon
    projected_max_loss: Decimal = Field(ge=0)
    hedge: bool = False


class PortfolioRiskService:
    def __init__(
        self,
        *,
        budgets: dict[Horizon, HorizonRiskBudget],
        aggregate_max_loss: Decimal,
        max_directional_exposure: Decimal,
        minimum_options_level: int,
    ) -> None:
        if aggregate_max_loss <= 0 or max_directional_exposure <= 0:
            raise ValueError("portfolio limits must be positive")
        if minimum_options_level < 0:
            raise ValueError("minimum_options_level must not be negative")
        self._budgets = dict(budgets)
        self._aggregate_max_loss = aggregate_max_loss
        self._max_directional_exposure = max_directional_exposure
        self._minimum_options_level = minimum_options_level

    def evaluate(
        self,
        candidate: StrategyCandidate,
        thesis: GroundedThesis,
        snapshot: PortfolioSnapshot,
    ) -> RiskDecision:
        reasons: list[str] = []
        budget = self._budgets.get(candidate.horizon)
        if budget is None:
            return self._decision(RiskOutcome.ABSTAIN, candidate, ("budget_missing",))
        if candidate.horizon is not thesis.horizon:
            reasons.append("horizon_mismatch")
        if not budget.minimum_dte <= candidate.dte <= budget.maximum_dte:
            reasons.append("dte_outside_budget")
        if thesis.confidence < budget.confidence_threshold:
            reasons.append("confidence_below_threshold")
        if candidate.max_loss > budget.per_trade_max_loss:
            reasons.append("per_trade_max_loss_exceeded")
        if snapshot.options_level < self._minimum_options_level:
            reasons.append("options_level_insufficient")
        if candidate.max_loss > snapshot.buying_power:
            reasons.append("buying_power_insufficient")
        if (
            snapshot.last_trade_at is not None
            and (snapshot.observed_at - snapshot.last_trade_at).total_seconds()
            < budget.cooldown_seconds
        ):
            reasons.append("cooldown_active")

        same_horizon_loss = sum(
            (
                position.max_loss
                for position in snapshot.open_positions
                if position.horizon is candidate.horizon
            ),
            Decimal("0"),
        ) + sum(
            (
                reservation.max_loss
                for reservation in snapshot.active_reservations
                if reservation.horizon is candidate.horizon
            ),
            Decimal("0"),
        )
        if same_horizon_loss + candidate.max_loss > budget.aggregate_capacity:
            reasons.append("horizon_capacity_exceeded")
        aggregate_loss = sum(
            (position.max_loss for position in snapshot.open_positions), Decimal("0")
        ) + sum(
            (reservation.max_loss for reservation in snapshot.active_reservations), Decimal("0")
        )
        if aggregate_loss + candidate.max_loss > self._aggregate_max_loss:
            reasons.append("aggregate_max_loss_exceeded")

        underlying = thesis.beneficiaries[0] if thesis.beneficiaries else ""
        duplicate = any(
            position.underlying == underlying and position.catalyst == thesis.catalyst
            for position in snapshot.open_positions
        ) or any(
            reservation.underlying == underlying and reservation.catalyst == thesis.catalyst
            for reservation in snapshot.active_reservations
        )
        if duplicate:
            reasons.append("duplicate_catalyst")

        position_count = sum(
            position.horizon is candidate.horizon for position in snapshot.open_positions
        ) + sum(
            reservation.horizon is candidate.horizon
            for reservation in snapshot.active_reservations
        )
        if position_count >= budget.max_positions:
            reasons.append("max_positions_exceeded")

        current_exposure = snapshot.directional_exposure.get(underlying, Decimal("0"))
        candidate_sign = (
            Decimal("1")
            if candidate.direction == ThesisDirection.BULLISH.value
            else Decimal("-1")
        )
        projected_exposure = current_exposure + candidate_sign * candidate.max_loss
        hedge = bool(current_exposure and abs(projected_exposure) < abs(current_exposure))
        if not hedge and abs(projected_exposure) > self._max_directional_exposure:
            reasons.append("directional_concentration_exceeded")

        if reasons:
            outcome = RiskOutcome.ABSTAIN if "cooldown_active" in reasons else RiskOutcome.DENY
            return self._decision(outcome, candidate, tuple(sorted(set(reasons))), hedge=hedge)
        return self._decision(RiskOutcome.ALLOW, candidate, (), hedge=hedge)

    @staticmethod
    def _decision(
        outcome: RiskOutcome,
        candidate: StrategyCandidate,
        reasons: tuple[str, ...],
        *,
        hedge: bool = False,
    ) -> RiskDecision:
        return RiskDecision(
            outcome=outcome,
            reasons=reasons,
            horizon=candidate.horizon,
            projected_max_loss=candidate.max_loss,
            hedge=hedge,
        )
