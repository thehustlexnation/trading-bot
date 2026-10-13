import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paper_trading.alpaca_adapter import SubmittedOrderResult
from paper_trading.signal_builder import PaperTradeDecision


DEFAULT_AUDIT_PATH = Path("logs/paper_trading_decisions.jsonl")
DEFAULT_WATCH_JOURNAL_PATH = Path("logs/paper_trading_watch_journal.jsonl")


def _json_default(value: Any):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def decision_to_audit_record(
    decision: PaperTradeDecision,
    symbol: str,
    trading_date,
    source: str,
    dry_run: bool,
    order_result: SubmittedOrderResult | None = None,
) -> dict:
    plan = decision.plan
    payload = order_result.payload if order_result is not None else None

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol.upper(),
        "trading_date": str(trading_date),
        "source": source,
        "dry_run": dry_run,
        "approved": decision.approved,
        "reason": decision.reason,
        "signal": decision.signal,
        "plan": None if plan is None else {
            "strategy_version": plan.strategy_version,
            "position_side": plan.position_side,
            "entry_side": plan.entry_side,
            "quantity": plan.quantity,
            "entry_price_reference": plan.entry_price_reference,
            "stop_price": plan.stop_price,
            "target_price": plan.target_price,
            "planned_risk_dollars": plan.planned_risk_dollars,
            "notional_dollars": plan.notional_dollars,
            "risk_fraction": plan.risk_fraction,
        },
        "order": None if payload is None else {
            "submitted": order_result.submitted,
            "side": payload.side,
            "qty": payload.qty,
            "type": payload.type,
            "time_in_force": payload.time_in_force,
            "order_class": payload.order_class,
            "take_profit_limit_price": payload.take_profit_limit_price,
            "stop_loss_stop_price": payload.stop_loss_stop_price,
        },
    }


def append_audit_record(
    record: dict,
    path: Path = DEFAULT_AUDIT_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                record,
                default=_json_default,
                sort_keys=True,
            )
            + "\n"
        )


def append_watch_journal_record(
    record: dict,
    path: Path = DEFAULT_WATCH_JOURNAL_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                record,
                default=_json_default,
                sort_keys=True,
            )
            + "\n"
        )
