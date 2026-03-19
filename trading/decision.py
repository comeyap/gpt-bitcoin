"""Decision logic: normalization, risk policy, and position sizing adjustments."""

import json
import logging
from datetime import datetime

import config
from trading.utils import safe_float, clamp_percentage, append_reason
from trading.database import get_last_decision_time, compute_high_watermark
from trading.orderbook import analyze_orderbook_depth

logger = logging.getLogger("autotrade")


def normalize_decision(advice):
    """Parse GPT advice JSON into a normalized decision dict."""
    if not advice:
        return {"decision": "hold", "percentage": 0, "reason": "No advice returned"}
    try:
        data = json.loads(advice)
    except Exception as e:
        return {"decision": "hold", "percentage": 0, "reason": f"Invalid JSON: {e}"}

    decision = str(data.get("decision", "")).lower()
    if decision not in {"buy", "sell", "hold"}:
        return {"decision": "hold", "percentage": 0, "reason": "Invalid decision value"}

    default_pct = 0 if decision == "hold" else 100
    max_pct = config.MAX_BUY_PERCENT if decision == "buy" else config.MAX_SELL_PERCENT
    percentage = clamp_percentage(data.get("percentage"), default_pct, max_pct)
    reason = data.get("reason", "")
    if decision == "hold":
        percentage = 0
    return {"decision": decision, "percentage": percentage, "reason": reason}


def apply_volatility_adjustment(percentage, market_context):
    """Scale position size based on market volatility."""
    vol = safe_float(market_context.get("volatility"))
    if vol >= config.HIGH_VOLATILITY_THRESHOLD:
        return percentage * config.VOLATILITY_REDUCTION
    if 0 < vol <= config.LOW_VOLATILITY_THRESHOLD:
        return percentage * config.VOLATILITY_BOOST
    return percentage


def apply_regime_adjustment(decision_type, percentage, market_context):
    """Adjust position size based on market regime (trending/ranging)."""
    if not config.REGIME_DETECTION_ENABLED:
        return percentage
    regime = market_context.get("regime", "unknown")
    if regime == "unknown":
        return percentage
    if regime == "ranging":
        return percentage * config.REGIME_RANGING_SIZE_MULT
    if regime == "trending_up":
        if decision_type == "buy":
            return percentage * config.REGIME_TRENDING_SIZE_MULT
        elif decision_type == "sell":
            return percentage * config.REGIME_COUNTER_TREND_SIZE_MULT
    elif regime == "trending_down":
        if decision_type == "sell":
            return percentage * config.REGIME_TRENDING_SIZE_MULT
        elif decision_type == "buy":
            return percentage * config.REGIME_COUNTER_TREND_SIZE_MULT
    return percentage


def apply_tiered_take_profit(pnl_pct, momentum):
    """Return a sell decision if a tiered take-profit threshold is met."""
    for tier in reversed(config.TIERED_TAKE_PROFIT):
        if pnl_pct >= tier["threshold"]:
            if tier["condition"] == "always":
                return {
                    "decision": "sell",
                    "percentage": tier["sell_pct"],
                    "reason": (f"Tiered take-profit at {pnl_pct:.2%} "
                               f"(tier >= {tier['threshold']:.0%})"),
                }
            if tier["condition"] == "momentum_weakening" and momentum < 0:
                return {
                    "decision": "sell",
                    "percentage": tier["sell_pct"],
                    "reason": f"Tiered take-profit at {pnl_pct:.2%} with weakening momentum",
                }
    return None


def _compute_dynamic_stop_loss(avg_price, market_context):
    """Compute dynamic stop-loss distance based on ATR when enabled."""
    if not getattr(config, "DYNAMIC_STOP_LOSS_ENABLED", False):
        return config.STOP_LOSS_PCT
    atr = safe_float(market_context.get("atr"))
    if atr <= 0 or avg_price <= 0:
        return config.STOP_LOSS_PCT
    atr_stop = (atr * config.DYNAMIC_STOP_LOSS_ATR_MULT) / avg_price
    return max(config.STOP_LOSS_PCT, atr_stop)


def check_position_risk(current_price, avg_price, momentum=0.0, market_context=None):
    """Check stop-loss, trailing stop, and tiered take-profit.

    Returns a sell decision dict if a risk threshold is hit, otherwise None.
    """
    if avg_price <= 0 or current_price <= 0:
        return None

    pnl = (current_price - avg_price) / avg_price

    # Dynamic or fixed stop-loss
    stop_pct = _compute_dynamic_stop_loss(avg_price, market_context or {})
    if pnl <= -stop_pct:
        return {
            "decision": "sell",
            "percentage": config.STOP_LOSS_SELL_PCT,
            "reason": f"Stop-loss triggered at {pnl:.2%} (threshold {stop_pct:.2%})",
        }

    if config.TRAILING_STOP_ENABLED and current_price > avg_price:
        hw = compute_high_watermark(current_price, avg_price)
        if hw > 0:
            drawdown = (hw - current_price) / hw
            if drawdown >= config.TRAILING_STOP_PCT:
                return {
                    "decision": "sell",
                    "percentage": config.TRAILING_STOP_SELL_PCT,
                    "reason": (f"Trailing stop: price {current_price:,.0f} "
                               f"dropped {drawdown:.2%} from high {hw:,.0f}"),
                    "high_watermark": hw,
                }

    tp = apply_tiered_take_profit(pnl, momentum)
    if tp:
        return tp

    return None


def _apply_buy_filters(pct, reason, trend, rsi, momentum):
    """Apply trend/RSI/momentum filters for buy decisions."""
    if trend == "down":
        pct *= 0.5
        reason = append_reason(reason, "Downtrend filter")
    if rsi >= 70:
        pct *= 0.4
        reason = append_reason(reason, "RSI overbought filter")
    if momentum < 0:
        pct *= 0.7
        reason = append_reason(reason, "Negative momentum filter")

    # RSI-based accumulation: boost buys on deep oversold
    if getattr(config, "RSI_OVERSOLD_ACCUMULATION_ENABLED", False):
        if rsi > 0 and rsi < getattr(config, "RSI_DEEP_OVERSOLD", 25):
            boost = getattr(config, "RSI_OVERSOLD_BOOST", 1.5)
            pct *= boost
            reason = append_reason(reason, f"RSI deep oversold boost ({rsi:.0f})")

    return pct, reason


def _apply_sell_filters(pct, reason, trend, rsi, momentum):
    """Apply trend/RSI/momentum filters for sell decisions."""
    if trend == "up":
        pct *= 0.6
        reason = append_reason(reason, "Uptrend filter")
    if rsi <= 30:
        pct *= 0.5
        reason = append_reason(reason, "RSI oversold filter")
    if momentum > 0:
        pct *= 0.8
        reason = append_reason(reason, "Positive momentum filter")
    return pct, reason


def _get_cooldown_minutes(market_context):
    """Return the appropriate cooldown based on market regime."""
    regime = market_context.get("regime", "unknown")
    trending_cooldown = getattr(config, "MIN_TRADE_INTERVAL_TRENDING", config.MIN_TRADE_INTERVAL_MINUTES)
    if regime in ("trending_up", "trending_down"):
        return trending_cooldown
    return config.MIN_TRADE_INTERVAL_MINUTES


def apply_risk_policy(decision, current_status, market_context):
    """Apply all risk filters and constraints to a raw decision."""
    try:
        status = json.loads(current_status)
    except Exception:
        status = {}

    orderbook = status.get("orderbook", {})
    units = orderbook.get("orderbook_units", [])
    current_price = safe_float(units[0].get("ask_price") if units else 0)
    btc_balance = safe_float(status.get("btc_balance"))
    krw_balance = safe_float(status.get("krw_balance"))
    avg_price = safe_float(status.get("btc_avg_buy_price"))

    dv = decision.get("decision", "hold")
    pct = safe_float(decision.get("percentage", 0))
    reason = decision.get("reason", "")
    trend = market_context.get("trend", "flat")
    rsi = safe_float(market_context.get("rsi"))
    momentum = safe_float(market_context.get("momentum"))

    # Cooldown (shorter in trending markets)
    cooldown_mins = _get_cooldown_minutes(market_context)
    last_time = get_last_decision_time()
    if last_time:
        mins = (datetime.now() - last_time).total_seconds() / 60
        if mins < cooldown_mins:
            return {
                "decision": "hold", "percentage": 0,
                "reason": f"Cooldown ({mins:.1f}m since last trade)",
                "_skip_save": True,
            }

    # Stop-loss, trailing stop & tiered take-profit
    if btc_balance > 0:
        risk_decision = check_position_risk(current_price, avg_price, momentum, market_context)
        if risk_decision:
            return risk_decision

    # Orderbook depth for buys
    if dv == "buy" and krw_balance > 0 and pct > 0:
        depth = analyze_orderbook_depth(orderbook, krw_balance * (pct / 100))
        if depth["slippage_pct"] > config.MAX_SLIPPAGE_PCT:
            pct *= 0.5
            reason = append_reason(reason, f"High slippage ({depth['slippage_pct']:.3%})")

    # Trend / RSI / momentum filters
    if dv == "buy":
        pct, reason = _apply_buy_filters(pct, reason, trend, rsi, momentum)
    elif dv == "sell":
        pct, reason = _apply_sell_filters(pct, reason, trend, rsi, momentum)

    if dv in {"buy", "sell"}:
        pct = apply_volatility_adjustment(pct, market_context)
        pct = apply_regime_adjustment(dv, pct, market_context)
        regime = market_context.get("regime", "unknown")
        if regime != "unknown":
            reason = append_reason(reason, f"Regime: {regime}")
        max_pct = config.MAX_BUY_PERCENT if dv == "buy" else config.MAX_SELL_PERCENT
        pct = clamp_percentage(pct, 0, max_pct)

        if dv == "buy" and 0 < pct < config.MIN_BUY_PCT_FLOOR:
            pct = config.MIN_BUY_PCT_FLOOR
            reason = append_reason(reason, "Floor applied")

    # Minimum order checks
    if dv == "buy":
        if krw_balance < config.MIN_ORDER_AMOUNT:
            return {"decision": "hold", "percentage": 0, "reason": "Insufficient KRW"}
        if krw_balance * (pct / 100) < config.MIN_ORDER_AMOUNT:
            return {"decision": "hold", "percentage": 0, "reason": "Order below minimum"}
    elif dv == "sell":
        if btc_balance <= 0:
            return {"decision": "hold", "percentage": 0, "reason": "No BTC to sell"}
        if current_price * btc_balance * (pct / 100) < config.MIN_ORDER_AMOUNT:
            return {"decision": "hold", "percentage": 0, "reason": "Order below minimum"}

    return {
        "decision": dv,
        "percentage": pct if dv != "hold" else 0,
        "reason": reason,
    }
