"""Sanitized, read-only evidence for hypothetical quote-based trade marks."""


def quote_spread_bps(quote):
    if not quote:
        return None
    bid, ask = float(quote["bid"]), float(quote["ask"])
    return (ask - bid) / ((ask + bid) / 2) * 10_000


def trade_diagnostics(payload):
    entry_quote = payload.get("quote")
    exit_quote = payload.get("exit_quote")
    risk = payload.get("risk")
    pnl = payload.get("pnl")
    reasons = dict(payload.get("unverified_reasons", {}))
    if payload.get("path_gap") and not reasons:
        reasons["LEGACY_UNVERIFIED_PATH"] = {"reason": "The older worker did not retain detailed gap evidence."}
    return {
        "symbol": payload.get("symbol"), "strategy": payload.get("strategy"),
        "side": payload.get("side"), "entry": payload.get("entry"), "stop": payload.get("stop"),
        "target": payload.get("target"), "quantity": payload.get("quantity"),
        "entry_time": payload.get("entry_time"), "exit_time": payload.get("exit_time"),
        "observed_exit": payload.get("exit"), "outcome": payload.get("outcome"),
        "planned_risk_dollars": risk, "observed_pnl_dollars": pnl,
        "r_calculated_from_record": pnl / risk if pnl is not None and risk else None,
        "entry_quote": entry_quote, "exit_quote": exit_quote,
        "entry_spread_bps": quote_spread_bps(entry_quote), "exit_spread_bps": quote_spread_bps(exit_quote),
        "verified_path": not payload.get("path_gap", False), "unverified_reasons": reasons,
        "code_sha_at_entry": payload.get("code_sha"),
        "basis": "Observed quote simulation; no broker order, stop execution or fill is established.",
    }
