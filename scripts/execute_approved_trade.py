import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from paper_trading.alpaca_adapter import submit_bracket_order
from paper_trading.broker import (
    create_alpaca_paper_trading_client,
    symbol_is_clear_to_trade,
)
from paper_trading.notifications import notify_slack
from paper_trading.order_plan import build_fixed_risk_bracket_order_plan


def parse_args():
    parser = argparse.ArgumentParser(
        description="Execute a human-approved Alpaca paper bracket order."
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--direction", choices=["LONG", "SHORT"], required=True)
    parser.add_argument("--entry", type=float, required=True)
    parser.add_argument("--stop", type=float, required=True)
    parser.add_argument("--target", type=float, default=None)
    parser.add_argument("--risk-dollars", type=float, required=True)
    parser.add_argument("--target-r", type=float, default=2.0)
    parser.add_argument("--submit-paper", action="store_true")
    parser.add_argument("--slack-webhook-url", default=None)
    return parser.parse_args()


def calculate_target(direction: str, entry: float, stop: float, target_r: float) -> float:
    direction = direction.upper()
    if direction == "LONG":
        risk = entry - stop
        if risk <= 0:
            raise ValueError("LONG stop must be below entry.")
        return entry + risk * target_r
    if direction == "SHORT":
        risk = stop - entry
        if risk <= 0:
            raise ValueError("SHORT stop must be above entry.")
        return entry - risk * target_r
    raise ValueError(f"Invalid direction: {direction}")


def main():
    args = parse_args()
    target = (
        args.target
        if args.target is not None
        else calculate_target(
            args.direction,
            args.entry,
            args.stop,
            args.target_r,
        )
    )

    plan = build_fixed_risk_bracket_order_plan(
        symbol=args.symbol,
        strategy_version=args.strategy,
        direction=args.direction,
        entry_price=args.entry,
        stop_price=args.stop,
        target_price=target,
        risk_dollars=args.risk_dollars,
    )

    if not plan.is_tradeable:
        raise SystemExit("REJECTED: ZERO_QUANTITY")

    client = create_alpaca_paper_trading_client()
    if not symbol_is_clear_to_trade(client, args.symbol):
        raise SystemExit("REJECTED: SYMBOL_HAS_OPEN_POSITION_OR_ORDER")

    result = submit_bracket_order(
        trading_client=client,
        plan=plan,
        dry_run=not args.submit_paper,
    )

    payload = result.payload
    mode = "PAPER SUBMITTED" if result.submitted else "DRY RUN"
    message = (
        f"{mode} APPROVED TRADE - {plan.symbol}\n"
        f"Strategy: {plan.strategy_version}\n"
        f"Action: {payload.side.upper()} {payload.qty} shares\n"
        f"Entry reference: {plan.entry_price_reference:.2f}\n"
        f"Stop: {payload.stop_loss_stop_price:.2f}\n"
        f"Target: {payload.take_profit_limit_price:.2f}\n"
        f"Planned risk: ${plan.planned_risk_dollars:.2f}\n"
        f"Notional: ${plan.notional_dollars:.2f}"
    )

    print(message)
    notify_slack(
        webhook_url=args.slack_webhook_url,
        message=message,
        enabled=bool(args.slack_webhook_url),
    )


if __name__ == "__main__":
    main()
