import pytest

from paper_trading.alpaca_adapter import build_alpaca_bracket_order_payload
from paper_trading.alpaca_adapter import submit_bracket_order
from paper_trading.order_plan import build_bracket_order_plan


class FakeTradingClient:
    def __init__(self):
        self.submitted = []

    def submit_order(self, order_request):
        self.submitted.append(order_request)
        return {"id": "order-1"}


def _long_plan():
    return build_bracket_order_plan(
        symbol="AAPL",
        strategy_version="ORB_RETEST_RECLAIM_BODY_2R",
        direction="LONG",
        entry_price=100.0,
        stop_price=98.123,
        target_price=104.456,
        account_equity=10_000.0,
        risk_fraction=0.01,
    )


def test_build_alpaca_bracket_order_payload_for_long_plan():
    plan = _long_plan()

    payload = build_alpaca_bracket_order_payload(plan)

    assert payload.symbol == "AAPL"
    assert payload.side == "buy"
    assert payload.qty == 53
    assert payload.type == "market"
    assert payload.time_in_force == "day"
    assert payload.order_class == "bracket"
    assert payload.take_profit_limit_price == 104.46
    assert payload.stop_loss_stop_price == 98.12


def test_build_alpaca_bracket_order_payload_for_short_plan():
    plan = build_bracket_order_plan(
        symbol="AAPL",
        strategy_version="ORB_RETEST_RECLAIM_BODY_2R",
        direction="SHORT",
        entry_price=100.0,
        stop_price=102.0,
        target_price=96.0,
        account_equity=10_000.0,
        risk_fraction=0.01,
    )

    payload = build_alpaca_bracket_order_payload(plan)

    assert payload.side == "sell"
    assert payload.qty == 50
    assert payload.take_profit_limit_price == 96.0
    assert payload.stop_loss_stop_price == 102.0


def test_build_alpaca_bracket_order_payload_rejects_zero_quantity():
    plan = build_bracket_order_plan(
        symbol="AAPL",
        strategy_version="ORB_RETEST_RECLAIM_BODY_2R",
        direction="LONG",
        entry_price=100.0,
        stop_price=98.0,
        target_price=104.0,
        account_equity=1_000.0,
        risk_fraction=0.001,
    )

    with pytest.raises(ValueError, match="zero quantity"):
        build_alpaca_bracket_order_payload(plan)


def test_submit_bracket_order_dry_run_does_not_call_broker():
    client = FakeTradingClient()

    result = submit_bracket_order(
        trading_client=client,
        plan=_long_plan(),
        dry_run=True,
    )

    assert not result.submitted
    assert result.dry_run
    assert result.payload.symbol == "AAPL"
    assert client.submitted == []


def test_submit_bracket_order_submission_is_disabled_until_lifecycle_is_validated():
    client = FakeTradingClient()

    with pytest.raises(RuntimeError, match="PAPER_SUBMISSION_DISABLED"):
        submit_bracket_order(trading_client=client, plan=_long_plan(), dry_run=False)
    assert client.submitted == []
