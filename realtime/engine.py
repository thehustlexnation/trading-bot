from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from paper_trading.config import PaperTradingConfig
from paper_trading.htf_breakout import build_htf_breakout_paper_trade_decision
from paper_trading.order_plan import build_bracket_order_plan
from paper_trading.signal_builder import build_retest_body_paper_trade_decision
from paper_trading.vwap_ema_cross import build_vwap_ema9_cross_paper_trade_decision
from realtime.config import GROUPS, clock
from realtime.journal import serializable, stable_id, utc_string
from realtime.market import NY, MarketState, aware_timestamp, with_entry_observation
from scripts.paper_trade import (build_relative_strength_context, final_policy_rejection,
                                 orb_failed_reclaim_reason, WatchedTrade, add_intraday_vwap)


class ShadowEngine:
    """A broker-free evaluator. Approved means a hypothetical portfolio reservation."""

    def __init__(self, config, journal, session, owner, daily=None, code_sha="unknown", now_fn=None):
        self.config = config
        self.journal = journal
        self.session = session
        self.owner = owner
        self.code_sha = code_sha
        self.now_fn = now_fn
        self.market = MarketState(session, config.feed, journal)
        self.daily = daily or {}
        self.macro = {"status": "unavailable", "reason": "not fetched", "events": []}
        self.macro_received_at = None
        self.seen = {}
        self.last_reasons = {}
        self.recorded = {}
        self.positions = {row["symbol"]: row["payload"] for row in journal.shadow_positions(session, True)}
        self.claimed = {row["symbol"] for row in journal.shadow_positions(session)}
        for symbol, payload in self.positions.items():
            payload["path_gap"] = True
            journal.mark_path_gap(session, symbol)
        self.stopped = False

    def set_macro(self, context, now):
        self.macro = context
        self.macro_received_at = aware_timestamp(now)
        self.journal.event(self.session, "MACRO_CONTEXT", {"asof": now, "context": context})

    def ingest_bar(self, message, now):
        return self.market.bar(message, now)

    def ingest_quote(self, message, now):
        previous = self.market.quotes.get(str(message["symbol"]).upper())
        quote = self.market.quote(message, now)
        if quote is None:
            return
        position = self.positions.get(quote.symbol)
        if position is None or quote.rejection(now, self.config.max_quote_age_seconds, float("inf")):
            return
        if previous is not None and (quote.timestamp - previous.timestamp).total_seconds() > self.config.max_quote_age_seconds:
            position["path_gap"] = True
            self.journal.mark_path_gap(self.session, quote.symbol)
        if quote.timestamp < aware_timestamp(position["entry_time"]):
            return
        price = quote.bid if position["side"] == "long" else quote.ask
        stop_hit = price <= position["stop"] if position["side"] == "long" else price >= position["stop"]
        target_hit = price >= position["target"] if position["side"] == "long" else price <= position["target"]
        if stop_hit or target_hit:
            self.close(quote.symbol, position["target"] if target_hit else price, "STOP" if stop_hit else "TARGET", now)
        elif aware_timestamp(now).tz_convert(NY).time() >= clock(self.config.cutoff):
            self.close(quote.symbol, price, "CUTOFF", now)
        elif not position.get("one_r_reached"):
            one_r = position["entry"] + (position["entry"] - position["stop"])
            reached = price >= one_r if position["side"] == "long" else price <= one_r
            if reached:
                self.journal.mark_one_r(self.session, quote.symbol)
                position["one_r_reached"] = True

    def reason(self, symbol, strategy, reason, now, detail=None):
        self.last_reasons[(symbol, strategy)] = reason
        record = {"symbol": symbol, "strategy": strategy, "reason": reason,
                  "asof": now, "config_hash": self.config.fingerprint,
                  "code_sha": self.code_sha, "detail": detail}
        key = stable_id(self.session, symbol, strategy, reason, aware_timestamp(now).floor("min"), detail)
        if self.recorded.get((symbol, strategy)) != key:
            self.journal.event(self.session, "DECISION", record, key)
            self.recorded[(symbol, strategy)] = key
        return reason

    def candidate(self, symbol, strategy, frame, quote, now):
        paper = PaperTradingConfig(risk_fraction=self.config.risk_fraction,
            max_trade_risk_fraction=self.config.risk_fraction,
            max_notional_fraction=self.config.max_notional_fraction,
            max_daily_loss_fraction=self.config.daily_loss_fraction)
        input_frame = with_entry_observation(frame, quote, now)
        if strategy == "ORB_RETEST_RECLAIM_BODY_2R":
            return build_retest_body_paper_trade_decision(input_frame, symbol, self.config.equity, paper)
        if strategy == "HTF_BREAKOUT_RETEST_2R":
            return build_htf_breakout_paper_trade_decision(
                self.daily.get(symbol, pd.DataFrame()), input_frame, symbol, self.config.equity, paper)
        if strategy == "VWAP_EMA9_CROSS_2R":
            return build_vwap_ema9_cross_paper_trade_decision(input_frame, symbol, self.config.equity, paper)
        raise ValueError("Unreviewed strategy is not enabled in the shadow runtime.")

    def evaluate_lane(self, symbol, strategy, now):
        if (symbol, strategy) not in self.config.lanes:
            return "UNCONFIGURED_LANE"
        if self.stopped:
            return "STOPPED"
        local_now = aware_timestamp(now).tz_convert(NY)
        if local_now.date().isoformat() != self.session:
            return self.reason(symbol, strategy, "SESSION_DATE_MISMATCH", now)
        if local_now.time() < clock(self.config.watch_start):
            return "BEFORE_WATCH_START"
        if local_now.time() >= clock(self.config.last_entry):
            return self.reason(symbol, strategy, "AFTER_LAST_ENTRY", now)
        if symbol in self.claimed:
            return "SYMBOL_ALREADY_EVALUATED"
        frame, rejection = self.market.ready_frame(symbol, now)
        if rejection:
            return self.reason(symbol, strategy, rejection, now)
        quote = self.market.quotes.get(symbol)
        if quote is None:
            return self.reason(symbol, strategy, "QUOTE_UNAVAILABLE", now)
        rejection = quote.rejection(now, self.config.max_quote_age_seconds, self.config.max_spread_bps)
        if rejection:
            return self.reason(symbol, strategy, rejection, now)
        benchmark = "SPY" if symbol == "QQQ" else "QQQ"
        benchmark_frame, rejection = self.market.ready_frame(benchmark, now)
        if rejection:
            return self.reason(symbol, strategy, "MARKET_CONTEXT_UNAVAILABLE", now)
        snapshot_key = (frame.iloc[-1]["timestamp"], benchmark_frame.iloc[-1]["timestamp"],
                        frame.iloc[-1]["received_at"], benchmark_frame.iloc[-1]["received_at"])
        if self.seen.get((symbol, strategy)) == snapshot_key:
            return self.last_reasons.get((symbol, strategy), "ALREADY_EVALUATED")
        if self.macro_received_at is None or (aware_timestamp(now) - self.macro_received_at).total_seconds() > 600:
            return self.reason(symbol, strategy, "MACRO_CONTEXT_UNAVAILABLE", now)
        decision = self.candidate(symbol, strategy, frame, quote, now)
        if not decision.approved or decision.plan is None:
            self.seen[(symbol, strategy)] = snapshot_key
            return self.reason(symbol, strategy, decision.reason, now, decision.signal)
        signal = decision.signal
        entry_time = aware_timestamp(signal["entry_timestamp"])
        confirmation = aware_timestamp(signal["confirmation_timestamp"])
        age = (aware_timestamp(now) - entry_time).total_seconds()
        if confirmation >= entry_time or age < 0 or age > self.config.max_signal_age_seconds:
            self.seen[(symbol, strategy)] = snapshot_key
            return self.reason(symbol, strategy, "STALE_OR_UNCONFIRMED_SIGNAL", now, signal)
        # Context shares the signal's cutoff, not later bars that could change its grade.
        stock_asof = frame[frame["timestamp"] < entry_time]
        benchmark_asof = benchmark_frame[benchmark_frame["timestamp"] < entry_time]
        signal["relative_strength"] = build_relative_strength_context(symbol, decision.plan.position_side,
            stock_asof, benchmark_asof, benchmark)
        args = SimpleNamespace(last_entry=self.config.last_entry, allow_high_impact_news=False)
        rejection = final_policy_rejection(args, strategy, decision, self.macro, now)
        if rejection:
            self.seen[(symbol, strategy)] = snapshot_key
            return self.reason(symbol, strategy, rejection, now, signal)
        approval_time = self.now_fn() if self.now_fn else now
        if aware_timestamp(approval_time).tz_convert(NY).time() >= clock(self.config.last_entry):
            return self.reason(symbol, strategy, "AFTER_LAST_ENTRY", approval_time)
        rejection = quote.rejection(approval_time, self.config.max_quote_age_seconds, self.config.max_spread_bps)
        if rejection:
            return self.reason(symbol, strategy, rejection, approval_time)
        if (aware_timestamp(approval_time) - entry_time).total_seconds() > self.config.max_signal_age_seconds:
            return self.reason(symbol, strategy, "STALE_OR_UNCONFIRMED_SIGNAL", approval_time)
        entry = quote.ask if decision.plan.position_side == "long" else quote.bid
        if abs(entry / decision.plan.entry_price_reference - 1) * 10_000 > self.config.max_entry_drift_bps:
            return self.reason(symbol, strategy, "ENTRY_DRIFT_LIMIT", now, signal)
        risk = entry - decision.plan.stop_price if decision.plan.position_side == "long" else decision.plan.stop_price - entry
        if risk <= 0:
            return self.reason(symbol, strategy, "INVALID_RISK_AT_QUOTE", now)
        target = entry + risk * 2 if decision.plan.position_side == "long" else entry - risk * 2
        plan = build_bracket_order_plan(symbol, strategy, signal["direction"], entry,
            decision.plan.stop_price, target, self.config.equity, self.config.risk_fraction,
            max_notional_fraction=self.config.max_notional_fraction)
        if not plan.is_tradeable:
            return self.reason(symbol, strategy, "ZERO_QUANTITY", now)
        payload = {"symbol": symbol, "strategy": strategy, "side": plan.position_side,
            "entry": entry, "entry_time": aware_timestamp(approval_time), "stop": plan.stop_price,
            "target": plan.target_price, "quantity": plan.quantity, "risk": plan.planned_risk_dollars,
            "notional": plan.notional_dollars, "signal": signal,
            "quote": asdict(quote), "historical_entry_reference": decision.plan.entry_price_reference,
            "reference_type": "hypothetical executable quote; not a fill", "policy_version": self.config.policy_version,
            "config_hash": self.config.fingerprint, "code_sha": self.code_sha}
        rejection = self.journal.open_shadow(self.session, symbol, GROUPS.get(symbol, symbol), payload,
            self.config, self.owner, approval_time)
        self.seen[(symbol, strategy)] = snapshot_key
        if rejection != "APPROVED":
            return self.reason(symbol, strategy, rejection, now, signal)
        self.positions[symbol] = serializable(payload)
        self.claimed.add(symbol)
        self.reason(symbol, strategy, "APPROVED_SHADOW", approval_time, payload)
        return "APPROVED_SHADOW"

    def tick(self, now):
        if aware_timestamp(now).tz_convert(NY).time() >= clock(self.config.cutoff):
            for symbol in list(self.positions):
                quote = self.market.quotes.get(symbol)
                if quote and not quote.rejection(now, self.config.max_quote_age_seconds, float("inf")):
                    price = quote.bid if self.positions[symbol]["side"] == "long" else quote.ask
                    self.close(symbol, price, "CUTOFF", now)
            return
        for symbol, position in list(self.positions.items()):
            quote = self.market.quotes.get(symbol)
            if not quote or quote.rejection(now, self.config.max_quote_age_seconds, float("inf")):
                continue
            frame, rejection = self.market.ready_frame(symbol, now)
            if rejection or frame.empty:
                continue
            last = add_intraday_vwap(frame).iloc[-1]
            if last["timestamp"] < aware_timestamp(position["entry_time"]).ceil("min"):
                continue
            trade = WatchedTrade(symbol, position["strategy"],
                SimpleNamespace(position_side=position["side"]), position["entry_time"],
                position["signal"], one_r_sent=position.get("one_r_reached", False))
            reason = orb_failed_reclaim_reason(trade, last)
            if reason:
                self.close(symbol, quote.bid if position["side"] == "long" else quote.ask, reason, now)
        for symbol, strategy in self.config.lanes:
            try:
                self.evaluate_lane(symbol, strategy, now)
            except Exception as exc:
                self.reason(symbol, strategy, "LANE_ERROR", now, {"error_type": type(exc).__name__})

    def close(self, symbol, price, reason, now):
        quote = self.market.quotes.get(symbol)
        result = self.journal.close_shadow(self.session, symbol, price, reason, now,
            asdict(quote) if quote else None)
        if result is not None:
            self.positions.pop(symbol, None)
            self.journal.event(self.session, "SHADOW_EXIT", result)
        return result

    def recap(self):
        rows = self.journal.shadow_positions(self.session)
        pnl = sum(row["pnl"] for row in rows if row["state"] == "CLOSED" and not row["payload"].get("path_gap"))
        uncertain = [row["symbol"] for row in rows if row["payload"].get("path_gap")]
        unresolved = [row["symbol"] for row in rows if row["state"] == "OPEN"]
        lines = [f"Streaming shadow recap | {self.session}",
            f"Mode: SHADOW ONLY | feed: {self.config.feed} | code: {self.code_sha}",
            f"Config: {self.config.fingerprint}",
            f"Coverage: {len(self.config.watchlist)} instruments / {len(self.config.lanes)} lanes",
            f"Hypothetical positions: {len(rows)} | closed P/L before fees: ${pnl:+.2f}",
            "Unverified-path results excluded: " + (", ".join(uncertain) if uncertain else "none"),
            "Unresolved shadow positions: " + (", ".join(unresolved) if unresolved else "none"),
            "", "Final lane status:"]
        lines.extend(f"{symbol} | {strategy} | {self.last_reasons.get((symbol, strategy), 'NOT_EVALUATED')}"
                     for symbol, strategy in self.config.lanes)
        lines.append("No broker orders. Results do not establish a live trading edge.")
        return "\n".join(lines)
