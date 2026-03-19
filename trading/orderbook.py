"""Orderbook depth analysis for slippage estimation."""

import logging

from trading.utils import safe_float

logger = logging.getLogger("autotrade")


def analyze_orderbook_depth(orderbook, amount_krw):
    """Estimate slippage for a given order size against the orderbook."""
    try:
        units = orderbook.get("orderbook_units", [])
        if not units:
            return {"slippage_pct": 0, "executable": True}

        best_ask = safe_float(units[0].get("ask_price"))
        if best_ask <= 0:
            return {"slippage_pct": 0, "executable": True}

        remaining = amount_krw
        total_btc = 0.0
        for u in units:
            ask_price = safe_float(u.get("ask_price"))
            ask_size = safe_float(u.get("ask_size"))
            if ask_price <= 0:
                continue
            available = ask_price * ask_size
            if remaining <= available:
                total_btc += remaining / ask_price
                remaining = 0
                break
            total_btc += ask_size
            remaining -= available

        if total_btc <= 0:
            return {"slippage_pct": 0, "executable": False}

        filled = amount_krw - remaining
        avg_price = filled / total_btc
        slippage = (avg_price - best_ask) / best_ask
        return {
            "slippage_pct": slippage,
            "executable": remaining == 0,
            "avg_fill_price": avg_price,
        }
    except Exception as e:
        logger.error(f"Error analyzing orderbook depth: {e}")
        return {"slippage_pct": 0, "executable": True}
