from dataclasses import dataclass

from paper_trading.order_plan import BracketOrderPlan


@dataclass(frozen=True)
class AlpacaBracketOrderPayload:
    symbol: str
    side: str
    qty: int
    type: str
    time_in_force: str
    order_class: str
    take_profit_limit_price: float
    stop_loss_stop_price: float


@dataclass(frozen=True)
class SubmittedOrderResult:
    submitted: bool
    dry_run: bool
    payload: AlpacaBracketOrderPayload
    broker_order: object = None


def build_alpaca_bracket_order_payload(
    plan: BracketOrderPlan,
) -> AlpacaBracketOrderPayload:
    if not plan.is_tradeable:
        raise ValueError("Cannot build an order payload for zero quantity.")

    return AlpacaBracketOrderPayload(
        symbol=plan.symbol,
        side=plan.entry_side,
        qty=plan.quantity,
        type="market",
        time_in_force=plan.time_in_force,
        order_class=plan.order_class,
        take_profit_limit_price=round(plan.target_price, 2),
        stop_loss_stop_price=round(plan.stop_price, 2),
    )


def build_alpaca_market_order_request(plan: BracketOrderPlan):
    if not plan.is_tradeable:
        raise ValueError("Cannot build an order request for zero quantity.")

    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import (
        MarketOrderRequest,
        StopLossRequest,
        TakeProfitRequest,
    )

    side = OrderSide.BUY if plan.entry_side == "buy" else OrderSide.SELL

    return MarketOrderRequest(
        symbol=plan.symbol,
        qty=plan.quantity,
        side=side,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(
            limit_price=round(plan.target_price, 2),
        ),
        stop_loss=StopLossRequest(
            stop_price=round(plan.stop_price, 2),
        ),
    )


def submit_bracket_order(
    trading_client,
    plan: BracketOrderPlan,
    dry_run: bool = True,
) -> SubmittedOrderResult:
    payload = build_alpaca_bracket_order_payload(plan)

    if dry_run:
        return SubmittedOrderResult(
            submitted=False,
            dry_run=True,
            payload=payload,
            broker_order=None,
        )

    raise RuntimeError(
        "PAPER_SUBMISSION_DISABLED: this release supports shadow evaluation only; "
        "broker-backed exits, reconciliation and recovery must be validated first."
    )
