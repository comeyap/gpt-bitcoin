"""Technical indicator calculation and support/resistance detection."""

import logging

import pandas_ta as ta

import config
from trading.utils import safe_float

logger = logging.getLogger("autotrade")


def add_indicators(df):
    """Add technical indicators to an OHLCV DataFrame."""
    df["SMA_10"] = ta.sma(df["close"], length=10)
    df["EMA_10"] = ta.ema(df["close"], length=10)
    df["RSI_14"] = ta.rsi(df["close"], length=14)

    stoch = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3, smooth_k=3)
    df = df.join(stoch)

    fast = df["close"].ewm(span=12, adjust=False).mean()
    slow = df["close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = fast - slow
    df["Signal_Line"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_Histogram"] = df["MACD"] - df["Signal_Line"]

    df["Middle_Band"] = df["close"].rolling(window=20).mean()
    std = df["close"].rolling(window=20).std()
    df["Upper_Band"] = df["Middle_Band"] + (std * 2)
    df["Lower_Band"] = df["Middle_Band"] - (std * 2)

    df["ATR_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    tp = (df["high"] + df["low"] + df["close"]) / 3
    df["VWAP"] = (tp * df["volume"]).cumsum() / df["volume"].cumsum()

    adx_data = ta.adx(df["high"], df["low"], df["close"], length=config.ADX_LENGTH)
    if adx_data is not None:
        df = df.join(adx_data)

    return df


def detect_support_resistance(df, window=10):
    """Detect nearest support and resistance levels."""
    try:
        highs = df["high"].rolling(window=window, center=True).max()
        lows = df["low"].rolling(window=window, center=True).min()
        resistance = df["high"][df["high"] == highs].dropna().unique()
        support = df["low"][df["low"] == lows].dropna().unique()
        price = df["close"].iloc[-1]
        res_levels = sorted([r for r in resistance if r > price])[:3]
        sup_levels = sorted([s for s in support if s < price], reverse=True)[:3]
        return {
            "nearest_resistance": res_levels[0] if res_levels else None,
            "nearest_support": sup_levels[0] if sup_levels else None,
            "resistance_levels": [float(r) for r in res_levels],
            "support_levels": [float(s) for s in sup_levels],
        }
    except Exception as e:
        logger.error(f"Error detecting support/resistance: {e}")
        return {
            "nearest_resistance": None, "nearest_support": None,
            "resistance_levels": [], "support_levels": [],
        }
