"""
Backtesting engine for gpt-bitcoin trading strategy.

Replays historical OHLCV data through the risk pipeline using a rule-based
strategy (no GPT calls).  Shares utility functions with autotrade_v3.py.

Usage:
    python3 backtest.py                        # default 180 days, 10M KRW
    python3 backtest.py --days 90 --capital 5000000
"""

import argparse
from datetime import datetime

import pyupbit
import pandas as pd

import config
from autotrade_v3 import (
    safe_float,
    clamp_percentage,
    add_indicators,
    apply_tiered_take_profit,
    apply_volatility_adjustment,
    apply_regime_adjustment,
)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def fetch_historical_data(days=config.BACKTEST_DAYS):
    """Fetch daily OHLCV data with paging (pyupbit max 200 per call)."""
    all_data = []
    remaining = days
    to_date = None

    while remaining > 0:
        count = min(remaining, 200)
        df = pyupbit.get_ohlcv("KRW-BTC", "day", count=count, to=to_date)
        if df is None or df.empty:
            break
        all_data.append(df)
        to_date = df.index[0].strftime("%Y%m%d")
        remaining -= len(df)

    if not all_data:
        raise ValueError("Failed to fetch historical data")

    combined = pd.concat(all_data).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    return add_indicators(combined)


# ---------------------------------------------------------------------------
# Rule-based strategy (replaces GPT)
# ---------------------------------------------------------------------------
def rule_based_strategy(row, prev_row, position_held):
    """Generate buy/sell/hold based on technical indicators.

    Buy  : RSI < 35 AND MACD golden cross (optionally near lower BB)
    Sell : RSI > 70 AND MACD death cross (optionally near upper BB)
    """
    rsi = safe_float(row.get("RSI_14"))
    macd = safe_float(row.get("MACD"))
    sig = safe_float(row.get("Signal_Line"))
    prev_macd = safe_float(prev_row.get("MACD")) if prev_row is not None else 0
    prev_sig = safe_float(prev_row.get("Signal_Line")) if prev_row is not None else 0
    close = safe_float(row.get("close"))
    lower_bb = safe_float(row.get("Lower_Band"))
    upper_bb = safe_float(row.get("Upper_Band"))

    macd_cross_up = (prev_macd <= prev_sig) and (macd > sig)
    macd_cross_down = (prev_macd >= prev_sig) and (macd < sig)
    near_lower = close <= lower_bb * 1.02 if lower_bb > 0 else False
    near_upper = close >= upper_bb * 0.98 if upper_bb > 0 else False

    # Strong buy signal
    if not position_held and rsi < 35 and macd_cross_up:
        pct = 40 if near_lower else 25
        return {
            "decision": "buy", "percentage": pct,
            "reason": f"RSI={rsi:.0f}, MACD cross up, BB={'near' if near_lower else 'mid'}",
        }

    # Strong sell signal
    if position_held and rsi > 70 and macd_cross_down:
        pct = 50 if near_upper else 30
        return {
            "decision": "sell", "percentage": pct,
            "reason": f"RSI={rsi:.0f}, MACD cross down, BB={'near' if near_upper else 'mid'}",
        }

    # Moderate buy
    if not position_held and rsi < 45 and macd > sig and near_lower:
        return {
            "decision": "buy", "percentage": 20,
            "reason": f"Moderate buy RSI={rsi:.0f}, near lower BB",
        }

    return {"decision": "hold", "percentage": 0, "reason": "No signal"}


# ---------------------------------------------------------------------------
# Simulation engine
# ---------------------------------------------------------------------------
class BacktestEngine:
    def __init__(self, df, initial_krw, initial_btc=0.0):
        self.df = df
        self.krw = initial_krw
        self.btc = initial_btc
        self.avg_buy_price = 0.0
        self.high_watermark = 0.0
        self.trades = []
        self.portfolio_history = []
        self.last_trade_idx = -999

    def run(self):
        # Start after warmup period for indicators
        start = min(26, len(self.df) - 1)
        for i in range(start, len(self.df)):
            row = self.df.iloc[i]
            prev_row = self.df.iloc[i - 1]
            price = safe_float(row["close"])
            timestamp = self.df.index[i]

            portfolio_val = self.krw + self.btc * price
            self.portfolio_history.append({
                "timestamp": timestamp, "value": portfolio_val,
                "price": price, "btc": self.btc, "krw": self.krw,
            })

            market_ctx = self._build_context(i)
            decision = rule_based_strategy(row, prev_row, self.btc > 0)
            decision = self._apply_risk(decision, price, market_ctx, i)

            if decision["decision"] == "buy" and decision["percentage"] > 0:
                self._execute_buy(price, decision["percentage"], timestamp, decision["reason"])
            elif decision["decision"] == "sell" and decision["percentage"] > 0:
                self._execute_sell(price, decision["percentage"], timestamp, decision["reason"])

    def _build_context(self, idx):
        row = self.df.iloc[idx]
        window = self.df.iloc[max(0, idx - 5):idx + 1]
        momentum = ((window["close"].iloc[-1] / window["close"].iloc[0]) - 1
                     if len(window) >= 2 else 0)
        returns = self.df["close"].iloc[max(0, idx - 23):idx + 1].pct_change()
        volatility = returns.std() if len(returns) > 1 else 0

        ema = safe_float(row.get("EMA_10"))
        sma = safe_float(row.get("SMA_10"))
        trend = "up" if ema > sma else ("down" if ema < sma else "flat")

        adx = safe_float(row.get(f"ADX_{config.ADX_LENGTH}"))
        dmp = safe_float(row.get(f"DMP_{config.ADX_LENGTH}"))
        dmn = safe_float(row.get(f"DMN_{config.ADX_LENGTH}"))
        if adx < config.ADX_TRENDING_THRESHOLD:
            regime = "ranging"
        elif dmp > dmn and ema >= sma:
            regime = "trending_up"
        elif dmn > dmp and ema <= sma:
            regime = "trending_down"
        else:
            regime = "ranging"

        return {
            "trend": trend,
            "rsi": safe_float(row.get("RSI_14")),
            "volatility": volatility,
            "momentum": momentum,
            "atr": safe_float(row.get("ATR_14")),
            "regime": regime,
            "adx": adx,
        }

    def _apply_risk(self, decision, price, ctx, idx):
        dv = decision["decision"]
        pct = decision["percentage"]
        reason = decision["reason"]

        # Cooldown (1 bar minimum)
        if idx - self.last_trade_idx < 1:
            return {"decision": "hold", "percentage": 0, "reason": "Cooldown"}

        # Stop-loss
        if self.btc > 0 and self.avg_buy_price > 0:
            pnl = (price - self.avg_buy_price) / self.avg_buy_price

            if pnl <= -config.STOP_LOSS_PCT:
                return {
                    "decision": "sell", "percentage": config.STOP_LOSS_SELL_PCT,
                    "reason": f"Stop-loss at {pnl:.2%}",
                }

            # Trailing stop
            if config.TRAILING_STOP_ENABLED and price > self.avg_buy_price:
                self.high_watermark = max(self.high_watermark, price)
                if self.high_watermark > 0:
                    dd = (self.high_watermark - price) / self.high_watermark
                    if dd >= config.TRAILING_STOP_PCT:
                        return {
                            "decision": "sell",
                            "percentage": config.TRAILING_STOP_SELL_PCT,
                            "reason": f"Trailing stop {dd:.2%} from {self.high_watermark:,.0f}",
                        }

            # Tiered take-profit
            tp = apply_tiered_take_profit(pnl, ctx.get("momentum", 0))
            if tp:
                return tp

        # Filters
        if dv in {"buy", "sell"}:
            trend = ctx.get("trend", "flat")
            rsi = safe_float(ctx.get("rsi"))
            momentum = safe_float(ctx.get("momentum"))

            if dv == "buy":
                if trend == "down":
                    pct *= 0.5
                if rsi >= 70:
                    pct *= 0.4
                if momentum < 0:
                    pct *= 0.7
            else:
                if trend == "up":
                    pct *= 0.6
                if rsi <= 30:
                    pct *= 0.5
                if momentum > 0:
                    pct *= 0.8

            pct = apply_volatility_adjustment(pct, ctx)
            pct = apply_regime_adjustment(dv, pct, ctx)
            max_pct = config.MAX_BUY_PERCENT if dv == "buy" else config.MAX_SELL_PERCENT
            pct = clamp_percentage(pct, 0, max_pct)

            # Apply minimum buy percentage floor
            if dv == "buy" and 0 < pct < config.MIN_BUY_PCT_FLOOR:
                pct = config.MIN_BUY_PCT_FLOOR

        # Min order
        if dv == "buy" and self.krw * (pct / 100) < config.MIN_ORDER_AMOUNT:
            return {"decision": "hold", "percentage": 0, "reason": "Below min order"}
        if dv == "sell" and self.btc <= 0:
            return {"decision": "hold", "percentage": 0, "reason": "No BTC"}

        decision["percentage"] = pct
        decision["reason"] = reason
        return decision

    def _execute_buy(self, price, percentage, timestamp, reason):
        amount_krw = self.krw * (percentage / 100)
        btc_bought = (amount_krw * config.FEE_RATE) / price

        total_btc = self.btc + btc_bought
        if total_btc > 0:
            self.avg_buy_price = (
                (self.btc * self.avg_buy_price + btc_bought * price) / total_btc
            )
        self.btc = total_btc
        self.krw -= amount_krw
        self.high_watermark = max(self.high_watermark, price)
        self.last_trade_idx = self.df.index.get_loc(timestamp)

        self.trades.append({
            "timestamp": timestamp, "action": "buy", "price": price,
            "percentage": percentage, "amount_krw": amount_krw,
            "btc_amount": btc_bought, "reason": reason,
        })

    def _execute_sell(self, price, percentage, timestamp, reason):
        btc_sold = self.btc * (percentage / 100)
        krw_received = btc_sold * price * config.FEE_RATE

        self.btc -= btc_sold
        self.krw += krw_received
        self.last_trade_idx = self.df.index.get_loc(timestamp)

        if self.btc <= 0.00000001:
            self.btc = 0.0
            self.avg_buy_price = 0.0
            self.high_watermark = 0.0

        self.trades.append({
            "timestamp": timestamp, "action": "sell", "price": price,
            "percentage": percentage, "krw_received": krw_received,
            "btc_amount": btc_sold, "reason": reason,
        })


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def compute_metrics(engine):
    history = engine.portfolio_history
    if not history:
        return {}

    values = [h["value"] for h in history]
    initial = values[0]
    final = values[-1]
    total_return = (final - initial) / initial

    # Max drawdown
    peak = values[0]
    max_dd = 0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd

    # Win rate (simplified: sells not caused by stop-loss are wins)
    sells = [t for t in engine.trades if t["action"] == "sell"]
    wins = sum(1 for t in sells if "stop-loss" not in t.get("reason", "").lower())
    win_rate = wins / len(sells) if sells else 0

    # Sharpe ratio (daily returns, annualized)
    daily_returns = pd.Series(values).pct_change().dropna()
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = (daily_returns.mean() / daily_returns.std()) * (365 ** 0.5)
    else:
        sharpe = 0

    return {
        "initial_capital": initial,
        "final_capital": final,
        "total_return": total_return,
        "max_drawdown": max_dd,
        "sharpe_ratio": sharpe,
        "total_trades": len(engine.trades),
        "total_buys": len([t for t in engine.trades if t["action"] == "buy"]),
        "total_sells": len(sells),
        "win_rate": win_rate,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def print_results(metrics, trades):
    print("=" * 60)
    print("  BACKTEST RESULTS")
    print("=" * 60)
    print(f"  Initial Capital:  {metrics['initial_capital']:>15,.0f} KRW")
    print(f"  Final Capital:    {metrics['final_capital']:>15,.0f} KRW")
    print(f"  Total Return:     {metrics['total_return']:>14.2%}")
    print(f"  Max Drawdown:     {metrics['max_drawdown']:>14.2%}")
    print(f"  Sharpe Ratio:     {metrics['sharpe_ratio']:>14.2f}")
    print(f"  Total Trades:     {metrics['total_trades']:>15d}")
    print(f"    Buys:           {metrics['total_buys']:>15d}")
    print(f"    Sells:          {metrics['total_sells']:>15d}")
    print(f"  Win Rate:         {metrics['win_rate']:>14.2%}")
    print("=" * 60)

    if trades:
        print(f"\n  TRADE LOG (last {min(20, len(trades))})")
        print("-" * 60)
        for t in trades[-20:]:
            ts = (t["timestamp"].strftime("%Y-%m-%d")
                  if hasattr(t["timestamp"], "strftime") else str(t["timestamp"]))
            print(f"  {ts}  {t['action']:>4}  {t['price']:>13,.0f}  "
                  f"{t['percentage']:>5.1f}%  {t['reason'][:35]}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest gpt-bitcoin strategy")
    parser.add_argument("--days", type=int, default=config.BACKTEST_DAYS,
                        help="Days of history to fetch")
    parser.add_argument("--capital", type=float, default=config.BACKTEST_INITIAL_KRW,
                        help="Starting capital in KRW")
    args = parser.parse_args()

    print(f"Fetching {args.days} days of historical data...")
    df = fetch_historical_data(args.days)
    print(f"Running backtest on {len(df)} candles...\n")

    engine = BacktestEngine(df, args.capital)
    engine.run()

    metrics = compute_metrics(engine)
    print_results(metrics, engine.trades)
