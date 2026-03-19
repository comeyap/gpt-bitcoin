"""Market data fetching, regime detection, and chart generation."""

import base64
import json
import logging
import time
from io import BytesIO

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
import pyupbit

import config
from trading.utils import safe_float
from trading.indicators import add_indicators, detect_support_resistance

logger = logging.getLogger("autotrade")

# Lazy init — set from autotrade_v3 main
upbit = None


def set_upbit(upbit_instance):
    global upbit
    upbit = upbit_instance


def get_current_status():
    orderbook = pyupbit.get_orderbook(ticker="KRW-BTC")
    if not orderbook:
        orderbook = {"timestamp": int(time.time() * 1000), "orderbook_units": []}
    current_time = orderbook.get("timestamp", int(time.time() * 1000))
    btc_balance = krw_balance = btc_avg_buy_price = 0
    try:
        for b in upbit.get_balances():
            if b["currency"] == "BTC":
                btc_balance = safe_float(b.get("balance"))
                btc_avg_buy_price = safe_float(b.get("avg_buy_price"))
            if b["currency"] == "KRW":
                krw_balance = safe_float(b.get("balance"))
    except Exception as e:
        logger.error(f"Error getting balances: {e}")

    return json.dumps({
        "current_time": current_time,
        "orderbook": orderbook,
        "btc_balance": btc_balance,
        "krw_balance": krw_balance,
        "btc_avg_buy_price": btc_avg_buy_price,
    })


def detect_market_regime(df_hourly):
    """Classify market as trending_up, trending_down, or ranging using ADX."""
    if not config.REGIME_DETECTION_ENABLED:
        return "unknown"
    try:
        latest = df_hourly.iloc[-1]
        adx = safe_float(latest.get(f"ADX_{config.ADX_LENGTH}"))
        dmp = safe_float(latest.get(f"DMP_{config.ADX_LENGTH}"))
        dmn = safe_float(latest.get(f"DMN_{config.ADX_LENGTH}"))
        ema = safe_float(latest.get("EMA_10"))
        sma = safe_float(latest.get("SMA_10"))

        if adx < config.ADX_TRENDING_THRESHOLD:
            return "ranging"
        if dmp > dmn and ema >= sma:
            return "trending_up"
        if dmn > dmp and ema <= sma:
            return "trending_down"
        return "ranging"
    except Exception as e:
        logger.error(f"Error detecting market regime: {e}")
        return "unknown"


def build_market_context(df_hourly):
    try:
        latest = df_hourly.iloc[-1]
        rsi = safe_float(latest.get("RSI_14"))
        ema = safe_float(latest.get("EMA_10"))
        sma = safe_float(latest.get("SMA_10"))
        atr = safe_float(latest.get("ATR_14"))
        vwap = safe_float(latest.get("VWAP"))
        trend = "up" if ema > sma else "down" if ema < sma else "flat"
        returns = df_hourly["close"].pct_change()
        volatility = safe_float(returns.rolling(window=24).std().iloc[-1])
        momentum = (
            safe_float((df_hourly["close"].iloc[-1] / df_hourly["close"].iloc[-6]) - 1)
            if len(df_hourly) >= 6 else 0.0
        )
        sr = detect_support_resistance(df_hourly)
        regime = detect_market_regime(df_hourly)
        adx = safe_float(latest.get(f"ADX_{config.ADX_LENGTH}"))
        return {
            "trend": trend, "rsi": rsi, "volatility": volatility,
            "momentum": momentum, "atr": atr, "vwap": vwap,
            "current_price": safe_float(df_hourly["close"].iloc[-1]),
            "support_resistance": sr,
            "regime": regime, "adx": adx,
        }
    except Exception as e:
        logger.error(f"Error building market context: {e}")
        return {
            "trend": "flat", "rsi": 0, "volatility": 0, "momentum": 0,
            "atr": 0, "vwap": 0, "current_price": 0, "support_resistance": {},
            "regime": "unknown", "adx": 0,
        }


def fetch_and_prepare_data():
    df_daily = pyupbit.get_ohlcv("KRW-BTC", "day", count=30)
    df_hourly = pyupbit.get_ohlcv("KRW-BTC", interval="minute60", count=24)
    if df_daily is None or df_hourly is None:
        raise ValueError("Failed to fetch OHLCV data from Upbit")

    df_daily = add_indicators(df_daily)
    df_hourly = add_indicators(df_hourly)

    combined = pd.concat([df_daily, df_hourly], keys=["daily", "hourly"])
    combined_json = combined.to_json(orient="split")
    market_ctx = build_market_context(df_hourly)
    return combined_json, market_ctx, df_hourly


def generate_chart_image(df_hourly):
    """Generate a candlestick chart with BB, MACD, RSI using mplfinance."""
    try:
        df = df_hourly.copy()
        df.index = pd.to_datetime(df.index)

        bb_upper = df["Upper_Band"]
        bb_lower = df["Lower_Band"]
        bb_mid = df["Middle_Band"]
        macd = df["MACD"]
        signal_line = df["Signal_Line"]
        hist = df["MACD_Histogram"]
        rsi = df["RSI_14"]
        rsi_70 = pd.Series(70.0, index=df.index)
        rsi_30 = pd.Series(30.0, index=df.index)

        hist_colors = ["#26a69a" if v >= 0 else "#ef5350" for v in hist.fillna(0)]

        apds = [
            mpf.make_addplot(bb_upper, color="steelblue", linestyle="dashed", width=0.7),
            mpf.make_addplot(bb_lower, color="steelblue", linestyle="dashed", width=0.7),
            mpf.make_addplot(bb_mid, color="steelblue", width=0.5, alpha=0.5),
            mpf.make_addplot(df["SMA_10"], color="orange", width=0.8),
            mpf.make_addplot(df["EMA_10"], color="cyan", width=0.8),
            mpf.make_addplot(macd, panel=2, color="blue", ylabel="MACD"),
            mpf.make_addplot(signal_line, panel=2, color="orange"),
            mpf.make_addplot(hist, panel=2, type="bar", color=hist_colors),
            mpf.make_addplot(rsi, panel=3, color="purple", ylabel="RSI"),
            mpf.make_addplot(rsi_70, panel=3, color="red", linestyle="--", width=0.5, alpha=0.5),
            mpf.make_addplot(rsi_30, panel=3, color="green", linestyle="--", width=0.5, alpha=0.5),
        ]

        fig, _ = mpf.plot(
            df, type="candle", style="charles",
            addplot=apds, volume=True,
            title="KRW-BTC 1H Chart",
            figratio=(16, 10), figscale=1.2,
            panel_ratios=(4, 1, 1, 1),
            returnfig=True,
        )

        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)

        with open(config.SCREENSHOT_PATH, "wb") as f:
            f.write(buf.getvalue())
        buf.seek(0)

        logger.info("Chart image generated successfully")
        return base64.b64encode(buf.read()).decode("utf-8")
    except Exception as e:
        logger.error(f"Error generating chart image: {e}")
        return ""
