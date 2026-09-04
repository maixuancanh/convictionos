from convictionos.domain.trading import IntentState
from convictionos.infrastructure.brokers import (
    BrokerLookupUnavailable,
    BrokerOrder,
    BrokerOrderStatus,
    BrokerPort,
    BrokerRejected,
    BrokerReplacementPending,
    UnknownSubmission,
)
from convictionos.infrastructure.store import Store


class ExecutionOrchestrator:
    def __init__(self, store: Store, broker: BrokerPort) -> None:
        self._store = store
        self._broker = broker

    async def execute(self, intent_id: str) -> BrokerOrder:
        intent = await self._store.load_intent(intent_id)
        claim = await self._store.claim_submission(intent_id)
        if claim.state in {IntentState.REJECTED, IntentState.CANCELED}:
            raise RuntimeError(f"intent already {claim.state.value}")
        if claim.should_submit:
            try:
                order = await self._broker.submit(intent)
            except BrokerReplacementPending as error:
                await self._store.mark_replacement_pending(intent_id, str(error))
                raise
            except BrokerRejected as error:
                await self._store.mark_not_filled(intent_id, IntentState.REJECTED, str(error))
                raise
            except UnknownSubmission:
                try:
                    recovered_order = await self._broker.get_by_client_order_id(
                        intent.idempotency_key
                    )
                except BrokerLookupUnavailable:
                    await self._store.mark_ack_unknown(
                        intent_id, "broker submission lookup unavailable after unknown ack"
                    )
                    raise
                except BrokerReplacementPending as error:
                    await self._store.mark_replacement_pending(intent_id, str(error))
                    raise
                except Exception as error:
                    await self._store.recover_submission(
                        intent_id, f"unexpected broker recovery error: {error}"
                    )
                    raise
                if recovered_order is None:
                    await self._store.recover_submission(
                        intent_id, "broker submission lookup returned no order"
                    )
                    raise RuntimeError("submission state remains unknown") from None
                order = recovered_order
            except Exception as error:
                await self._store.recover_submission(
                    intent_id, f"unexpected broker submit error: {error}"
                )
                raise
        else:
            stored_order = await self._broker.get_by_client_order_id(intent.idempotency_key)
            if stored_order is None:
                raise RuntimeError("stored submission is not queryable")
            order = stored_order
        if order.client_order_id != intent.idempotency_key:
            await self._store.recover_submission(
                intent_id, "broker returned an order for a different client order id"
            )
            raise ValueError("broker order client order id does not match intent idempotency key")
        if order.status is BrokerOrderStatus.REJECTED:
            await self._store.mark_not_filled(
                intent_id,
                IntentState.REJECTED,
                "broker reported rejected",
                broker_order_id=order.order_id,
            )
            return order
        if order.status is BrokerOrderStatus.CANCELED:
            await self._store.mark_not_filled(
                intent_id,
                IntentState.CANCELED,
                "broker reported canceled",
                broker_order_id=order.order_id,
            )
            return order
        if claim.state is not IntentState.RECONCILED:
            next_state = {
                BrokerOrderStatus.NEW: IntentState.SUBMITTING,
                BrokerOrderStatus.PARTIALLY_FILLED: IntentState.PARTIALLY_FILLED,
                BrokerOrderStatus.FILLED: IntentState.RECONCILED,
            }[order.status]
            await self._store.record_broker_order(
                intent_id,
                order.order_id,
                next_state,
            )
        return order
