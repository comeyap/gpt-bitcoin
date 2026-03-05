import os
import sys
import signal
import logging
import json
import time
import base64
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime
from io import BytesIO

from dotenv import load_dotenv
load_dotenv()

import pyupbit
import pandas as pd
import pandas_ta as ta
import requests
import schedule
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
from openai import OpenAI

import config

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
logger = logging.getLogger("autotrade")
shutdown_requested = False

# Lazy init — allows backtest.py to import without requiring API keys
try:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    upbit = pyupbit.Upbit(os.getenv("UPBIT_ACCESS_KEY"), os.getenv("UPBIT_SECRET_KEY"))
except Exception:
    client = None
    upbit = None


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def setup_logging():
    log_level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    logger.setLevel(log_level)
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    console = logging.StreamHandler()
    console.setLevel(log_level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = logging.FileHandler(config.LOG_FILE, encoding='utf-8')
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def handle_shutdown(sig, _frame):
    global shutdown_requested
    logger.info(f"Received signal {sig}, shutting down gracefully...")
    shutdown_requested = True


def validate_config():
    required = {
        "UPBIT_ACCESS_KEY": os.getenv("UPBIT_ACCESS_KEY"),
        "UPBIT_SECRET_KEY": os.getenv("UPBIT_SECRET_KEY"),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        logger.critical(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)
    logger.info("Configuration validated successfully")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp_percentage(value, default_value, max_value):
    if value is None:
        value = default_value
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = default_value
    value = max(0.0, min(value, 100.0))
    return min(value, max_value)


def append_reason(reason, note):
    if not note:
        return reason
    return f"{reason} | {note}" if reason else note


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def initialize_db(db_path=config.DB_PATH):
    with sqlite3.connect(db_path) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME,
                decision TEXT,
                percentage REAL,
                reason TEXT,
                btc_balance REAL,
                krw_balance REAL,
                btc_avg_buy_price REAL,
                btc_krw_price REAL
            )
        ''')
        conn.commit()


def migrate_db(db_path=config.DB_PATH):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(decisions)")
        existing = {row[1] for row in cursor.fetchall()}
        new_columns = {"high_watermark": "REAL", "market_context": "TEXT"}
        for col, col_type in new_columns.items():
            if col not in existing:
                cursor.execute(f"ALTER TABLE decisions ADD COLUMN {col} {col_type}")
                logger.info(f"Migrated DB: added column '{col}'")
        conn.commit()


def save_decision_to_db(decision, current_status):
    try:
        status = json.loads(current_status) if isinstance(current_status, str) else current_status
        orderbook = status.get("orderbook", {})
        units = orderbook.get("orderbook_units", [])
        current_price = safe_float(units[0].get("ask_price")) if units else 0.0

        hw = decision.get("high_watermark")

        # Reset watermark when sell closes the position entirely
        if decision.get("decision") == "sell":
            btc = safe_float(status.get("btc_balance"))
            pct = safe_float(decision.get("percentage", 0))
            remaining_btc = btc * (1 - pct / 100)
            if remaining_btc < 0.00000001:
                hw = 0.0
                logger.info("Position fully closed — high watermark reset to 0")

        with sqlite3.connect(config.DB_PATH) as conn:
            conn.execute('''
                INSERT INTO decisions
                    (timestamp, decision, percentage, reason, btc_balance, krw_balance,
                     btc_avg_buy_price, btc_krw_price, high_watermark, market_context)
                VALUES (datetime('now','localtime'), ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                decision.get("decision"),
                decision.get("percentage", 0),
                decision.get("reason", ""),
                status.get("btc_balance"),
                status.get("krw_balance"),
                status.get("btc_avg_buy_price"),
                current_price,
                hw,
                decision.get("market_context_summary", ""),
            ))
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving decision to DB: {e}")


def fetch_last_decisions(db_path=config.DB_PATH, num=config.DEFAULT_DECISIONS_LIMIT):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT timestamp, decision, percentage, reason,
                   btc_balance, krw_balance, btc_avg_buy_price
            FROM decisions ORDER BY timestamp DESC LIMIT ?
        ''', (num,))
        rows = cursor.fetchall()
    if not rows:
        return "No decisions found."
    out = []
    for row in rows:
        ts = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        out.append(str({
            "timestamp": int(ts.timestamp() * 1000),
            "decision": row[1], "percentage": row[2], "reason": row[3],
            "btc_balance": row[4], "krw_balance": row[5], "btc_avg_buy_price": row[6],
        }))
    return "\n".join(out)


def get_last_decision_time(db_path=config.DB_PATH):
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT timestamp FROM decisions ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            return datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
    except Exception as e:
        logger.error(f"Error fetching last decision time: {e}")
        return None


def get_high_watermark(db_path=config.DB_PATH):
    """Retrieve the most recent non-null high_watermark from the DB."""
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT high_watermark FROM decisions "
                "WHERE high_watermark IS NOT NULL "
                "ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            return safe_float(row[0]) if row else 0.0
    except Exception as e:
        logger.error(f"Error fetching high_watermark: {e}")
        return 0.0


def compute_high_watermark(current_price, avg_buy_price):
    """Compute the updated high watermark. Only meaningful when in a position."""
    if avg_buy_price <= 0 or current_price <= 0:
        return 0.0
    stored_hw = get_high_watermark()
    return max(stored_hw, avg_buy_price, current_price)


def check_position_risk(current_price, avg_price, momentum=0.0):
    """Shared stop-loss / trailing-stop / tiered take-profit check.

    Returns a sell decision dict if a risk threshold is hit, otherwise None.
    """
    if avg_price <= 0 or current_price <= 0:
        return None

    pnl = (current_price - avg_price) / avg_price

    # Absolute floor stop-loss
    if pnl <= -config.STOP_LOSS_PCT:
        return {
            "decision": "sell",
            "percentage": config.STOP_LOSS_SELL_PCT,
            "reason": f"Stop-loss triggered at {pnl:.2%}",
        }

    # Trailing stop (only when in profit)
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

    # Tiered take-profit
    tp = apply_tiered_take_profit(pnl, momentum)
    if tp:
        return tp

    return None


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------
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

    # ADX for market regime detection
    adx_data = ta.adx(df["high"], df["low"], df["close"], length=config.ADX_LENGTH)
    if adx_data is not None:
        df = df.join(adx_data)

    return df


def detect_support_resistance(df, window=10):
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
            # Bollinger Bands on main chart
            mpf.make_addplot(bb_upper, color="steelblue", linestyle="dashed", width=0.7),
            mpf.make_addplot(bb_lower, color="steelblue", linestyle="dashed", width=0.7),
            mpf.make_addplot(bb_mid, color="steelblue", width=0.5, alpha=0.5),
            # Moving averages on main chart
            mpf.make_addplot(df["SMA_10"], color="orange", width=0.8),
            mpf.make_addplot(df["EMA_10"], color="cyan", width=0.8),
            # MACD panel
            mpf.make_addplot(macd, panel=2, color="blue", ylabel="MACD"),
            mpf.make_addplot(signal_line, panel=2, color="orange"),
            mpf.make_addplot(hist, panel=2, type="bar", color=hist_colors),
            # RSI panel
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


# ---------------------------------------------------------------------------
# External data
# ---------------------------------------------------------------------------
def get_news_data():
    """Fetch BTC news from Google News RSS (no API key required)."""
    try:
        url = "https://news.google.com/rss/search?q=bitcoin+btc&hl=en&gl=US&ceid=US:en"
        resp = requests.get(url, timeout=config.API_TIMEOUT)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        items = root.findall(".//item")
        simplified = []
        for item in items[:10]:
            title = item.findtext("title", "No title")
            source = item.findtext("source", "Unknown")
            pub_date = item.findtext("pubDate", "")
            try:
                ts = int(datetime.strptime(
                    pub_date, "%a, %d %b %Y %H:%M:%S %Z"
                ).timestamp() * 1000)
            except (ValueError, TypeError):
                ts = int(datetime.now().timestamp() * 1000)
            simplified.append((title, source, ts))

        logger.info(f"Fetched {len(simplified)} news items from Google News")
        return str(simplified)
    except Exception as e:
        logger.error(f"Error fetching news: {e}")
        return "No news data available."


def fetch_fear_and_greed_index(limit=1, date_format=""):
    params = {"limit": limit, "format": "json", "date_format": date_format}
    try:
        resp = requests.get(
            "https://api.alternative.me/fng/",
            params=params, timeout=config.API_TIMEOUT,
        )
        resp.raise_for_status()
        return "".join(str(d) for d in resp.json().get("data", []))
    except Exception as e:
        logger.error(f"Error fetching Fear & Greed Index: {e}")
        return "No fear and greed data available."


# ---------------------------------------------------------------------------
# Orderbook depth analysis
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# GPT analysis
# ---------------------------------------------------------------------------
def get_instructions(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"Instructions file not found: {file_path}")
    except Exception as e:
        logger.error(f"Error reading instructions: {e}")
    return None


def analyze_data_with_gpt4(news_data, data_json, last_decisions,
                           fear_and_greed, current_status, chart_base64):
    instructions = get_instructions("instructions_v3.md")
    if not instructions:
        return None

    messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": news_data},
        {"role": "user", "content": data_json},
        {"role": "user", "content": last_decisions},
        {"role": "user", "content": fear_and_greed},
        {"role": "user", "content": current_status},
    ]
    if chart_base64:
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{chart_base64}"}},
            ],
        })

    try:
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=messages,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content
    except Exception as e:
        logger.error(f"GPT analysis error: {e}")
        return None


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------
def normalize_decision(advice):
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
    vol = safe_float(market_context.get("volatility"))
    if vol >= config.HIGH_VOLATILITY_THRESHOLD:
        return percentage * config.VOLATILITY_REDUCTION
    if 0 < vol <= config.LOW_VOLATILITY_THRESHOLD:
        return percentage * config.VOLATILITY_BOOST
    return percentage


def apply_regime_adjustment(decision_type, percentage, market_context):
    """Adjust position size based on market regime."""
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
    """Return a sell decision dict if a tiered take-profit threshold is met, else None."""
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


def apply_risk_policy(decision, current_status, market_context):
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

    # --- Cooldown ---
    last_time = get_last_decision_time()
    if last_time:
        mins = (datetime.now() - last_time).total_seconds() / 60
        if mins < config.MIN_TRADE_INTERVAL_MINUTES:
            return {
                "decision": "hold", "percentage": 0,
                "reason": f"Cooldown ({mins:.1f}m since last trade)",
                "_skip_save": True,
            }

    # --- Stop-loss, trailing stop & tiered take-profit ---
    if btc_balance > 0:
        risk_decision = check_position_risk(current_price, avg_price, momentum)
        if risk_decision:
            return risk_decision

    # --- Orderbook depth for buys ---
    if dv == "buy" and krw_balance > 0 and pct > 0:
        depth = analyze_orderbook_depth(orderbook, krw_balance * (pct / 100))
        if depth["slippage_pct"] > config.MAX_SLIPPAGE_PCT:
            pct *= 0.5
            reason = append_reason(reason, f"High slippage ({depth['slippage_pct']:.3%})")

    # --- Trend / RSI / momentum filters ---
    if dv in {"buy", "sell"}:
        if dv == "buy":
            if trend == "down":
                pct *= 0.5
                reason = append_reason(reason, "Downtrend filter")
            if rsi >= 70:
                pct *= 0.4
                reason = append_reason(reason, "RSI overbought filter")
            if momentum < 0:
                pct *= 0.7
                reason = append_reason(reason, "Negative momentum filter")
        else:
            if trend == "up":
                pct *= 0.6
                reason = append_reason(reason, "Uptrend filter")
            if rsi <= 30:
                pct *= 0.5
                reason = append_reason(reason, "RSI oversold filter")
            if momentum > 0:
                pct *= 0.8
                reason = append_reason(reason, "Positive momentum filter")

        pct = apply_volatility_adjustment(pct, market_context)
        pct = apply_regime_adjustment(dv, pct, market_context)
        regime = market_context.get("regime", "unknown")
        if regime != "unknown":
            reason = append_reason(reason, f"Regime: {regime}")
        max_pct = config.MAX_BUY_PERCENT if dv == "buy" else config.MAX_SELL_PERCENT
        pct = clamp_percentage(pct, 0, max_pct)

    # --- Minimum order checks ---
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


# ---------------------------------------------------------------------------
# DCA (Dollar Cost Averaging)
# ---------------------------------------------------------------------------
def load_dca_state():
    try:
        with open(config.DCA_STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"active": False, "tranches_remaining": 0,
                "original_percentage": 0, "last_tranche_time": None}


def save_dca_state(state):
    try:
        with open(config.DCA_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        logger.error(f"Error saving DCA state: {e}")


def apply_dca(decision):
    """Split buy decisions into multiple tranches via DCA.

    Only starts a NEW DCA sequence here (tranche 1).
    Pending tranches (2, 3, ...) are handled by execute_dca_tranche().
    """
    if not config.DCA_ENABLED:
        return decision
    if decision.get("decision") != "buy":
        # Cancel active DCA on sell/hold
        state = load_dca_state()
        if state.get("active"):
            state["active"] = False
            state["tranches_remaining"] = 0
            save_dca_state(state)
            decision["reason"] = append_reason(decision.get("reason", ""), "DCA cancelled")
        return decision

    state = load_dca_state()

    # DCA already active — pending tranches handled by execute_dca_tranche
    if state.get("active") and state.get("tranches_remaining", 0) > 0:
        return {"decision": "hold", "percentage": 0,
                "reason": "DCA already active — pending tranches handled separately",
                "_skip_save": True}

    # New DCA sequence — execute first tranche
    tranche_pct = decision["percentage"] / config.DCA_SPLITS
    state = {
        "active": True,
        "tranches_remaining": config.DCA_SPLITS - 1,
        "original_percentage": decision["percentage"],
        "last_tranche_time": datetime.now().isoformat(),
    }
    save_dca_state(state)

    decision["percentage"] = tranche_pct
    decision["reason"] = append_reason(
        decision.get("reason", ""), f"DCA tranche 1/{config.DCA_SPLITS}")
    return decision


def execute_dca_tranche():
    """Execute a pending DCA tranche directly — no GPT call."""
    state = load_dca_state()
    if not state.get("active") or state.get("tranches_remaining", 0) <= 0:
        return

    # Check interval
    last_time = state.get("last_tranche_time")
    if last_time:
        elapsed = (datetime.now() - datetime.fromisoformat(last_time)).total_seconds() / 60
        if elapsed < config.DCA_INTERVAL_MINUTES:
            logger.info(f"DCA waiting ({elapsed:.0f}m / {config.DCA_INTERVAL_MINUTES}m)")
            return

    # Quick risk check before executing tranche
    try:
        current_status = get_current_status()
        status = json.loads(current_status)
        units = status.get("orderbook", {}).get("orderbook_units", [])
        price = safe_float(units[0].get("ask_price") if units else 0)
        avg = safe_float(status.get("btc_avg_buy_price"))
        krw = safe_float(status.get("krw_balance"))

        # Cancel DCA if stop-loss level is breached
        if avg > 0 and price > 0:
            pnl = (price - avg) / avg
            if pnl <= -config.STOP_LOSS_PCT:
                logger.warning(f"DCA cancelled — stop-loss level breached ({pnl:.2%})")
                state["active"] = False
                state["tranches_remaining"] = 0
                save_dca_state(state)
                return
    except Exception as e:
        logger.error(f"DCA risk check failed: {e}")
        return

    # Execute tranche
    tranche_pct = state["original_percentage"] / config.DCA_SPLITS
    state["tranches_remaining"] -= 1
    state["last_tranche_time"] = datetime.now().isoformat()
    if state["tranches_remaining"] <= 0:
        state["active"] = False
    save_dca_state(state)

    tranche_num = config.DCA_SPLITS - state["tranches_remaining"]
    reason = f"DCA tranche {tranche_num}/{config.DCA_SPLITS}"
    logger.info(f"--- Executing {reason} at {tranche_pct:.1f}% ---")

    if krw * (tranche_pct / 100) < config.MIN_ORDER_AMOUNT:
        logger.warning("DCA tranche below minimum order — skipping")
        return

    execute_buy(tranche_pct)

    decision = {
        "decision": "buy",
        "percentage": tranche_pct,
        "reason": reason,
        "market_context_summary": "",
        "high_watermark": compute_high_watermark(price, avg) if avg > 0 else 0.0,
    }
    save_decision_to_db(decision, current_status)


def check_pending_dca():
    """Check and execute pending DCA tranches."""
    if not config.DCA_ENABLED:
        return
    state = load_dca_state()
    if not state.get("active") or state.get("tranches_remaining", 0) <= 0:
        return
    logger.info(f"--- DCA check: {state['tranches_remaining']} tranches remaining ---")
    execute_dca_tranche()


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def execute_buy(percentage):
    logger.info(f"Executing BUY at {percentage:.1f}% of KRW balance")
    try:
        krw = safe_float(upbit.get_balance("KRW"))
        amount = krw * (percentage / 100)
        if amount > config.MIN_ORDER_AMOUNT:
            result = upbit.buy_market_order("KRW-BTC", amount * config.FEE_RATE)
            logger.info(f"Buy order result: {result}")
        else:
            logger.warning(f"Buy amount {amount:.0f} below minimum")
    except Exception as e:
        logger.error(f"Buy execution failed: {e}")


def execute_sell(percentage):
    logger.info(f"Executing SELL at {percentage:.1f}% of BTC balance")
    try:
        btc = safe_float(upbit.get_balance("BTC"))
        amount = btc * (percentage / 100)
        price = pyupbit.get_orderbook(ticker="KRW-BTC")["orderbook_units"][0]["ask_price"]
        if price * amount > config.MIN_ORDER_AMOUNT:
            result = upbit.sell_market_order("KRW-BTC", amount)
            logger.info(f"Sell order result: {result}")
        else:
            logger.warning(f"Sell amount {price * amount:.0f} below minimum")
    except Exception as e:
        logger.error(f"Sell execution failed: {e}")


# ---------------------------------------------------------------------------
# Main cycles
# ---------------------------------------------------------------------------
def make_decision_and_execute():
    logger.info("=== Starting full analysis cycle ===")
    try:
        news_data = get_news_data()
        data_json, market_ctx, df_hourly = fetch_and_prepare_data()
        last_decisions = fetch_last_decisions()
        fear_greed = fetch_fear_and_greed_index(limit=config.FEAR_GREED_LIMIT)
        current_status = get_current_status()
        chart_b64 = generate_chart_image(df_hourly)
    except Exception as e:
        logger.error(f"Data fetch error: {e}", exc_info=True)
        return

    decision = None
    for attempt in range(config.MAX_RETRIES):
        try:
            advice = analyze_data_with_gpt4(
                news_data, data_json, last_decisions,
                fear_greed, current_status, chart_b64,
            )
            decision = normalize_decision(advice)
            decision = apply_risk_policy(decision, current_status, market_ctx)
            break
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{config.MAX_RETRIES} failed: {e}")
            if attempt < config.MAX_RETRIES - 1:
                time.sleep(config.RETRY_DELAY_SECONDS)

    if not decision:
        logger.error("Failed after max retries")
        return

    # Apply DCA splitting for buy decisions
    decision = apply_dca(decision)

    # Attach market context summary for DB
    decision["market_context_summary"] = json.dumps({
        "trend": market_ctx.get("trend"),
        "rsi": market_ctx.get("rsi"),
        "momentum": market_ctx.get("momentum"),
        "regime": market_ctx.get("regime"),
        "adx": market_ctx.get("adx"),
    })

    # Compute high_watermark for DB persistence
    status = json.loads(current_status)
    units = status.get("orderbook", {}).get("orderbook_units", [])
    cp = safe_float(units[0].get("ask_price") if units else 0)
    avg_bp = safe_float(status.get("btc_avg_buy_price"))
    decision["high_watermark"] = compute_high_watermark(cp, avg_bp)

    skip_save = decision.pop("_skip_save", False)
    logger.info(f"Decision: {decision}")

    try:
        pct = decision.get("percentage", 0)
        if decision["decision"] == "buy":
            execute_buy(pct)
        elif decision["decision"] == "sell":
            execute_sell(pct)
        if not skip_save:
            save_decision_to_db(decision, current_status)
    except Exception as e:
        logger.error(f"Execution/save error: {e}", exc_info=True)


def quick_risk_check():
    """Quick risk check without GPT — runs every 30 minutes."""
    logger.info("--- Quick risk check ---")
    try:
        current_status = get_current_status()
        status = json.loads(current_status)
        units = status.get("orderbook", {}).get("orderbook_units", [])
        price = safe_float(units[0].get("ask_price") if units else 0)
        btc = safe_float(status.get("btc_balance"))
        avg = safe_float(status.get("btc_avg_buy_price"))

        if btc <= 0 or avg <= 0 or price <= 0:
            logger.info("No position to monitor")
            return

        pnl = (price - avg) / avg
        logger.info(f"PnL={pnl:.2%}  Price={price:,.0f}")

        # Quick momentum from last 6 hours
        df_h = pyupbit.get_ohlcv("KRW-BTC", interval="minute60", count=6)
        momentum = 0
        if df_h is not None and len(df_h) >= 6:
            momentum = safe_float((df_h["close"].iloc[-1] / df_h["close"].iloc[0]) - 1)

        risk_decision = check_position_risk(price, avg, momentum)
        if risk_decision:
            logger.warning(f"Risk triggered: {risk_decision['reason']}")
            execute_sell(risk_decision["percentage"])
            save_decision_to_db(risk_decision, current_status)
    except Exception as e:
        logger.error(f"Risk check error: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    setup_logging()
    validate_config()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    initialize_db()
    migrate_db()

    # Run full analysis on startup
    make_decision_and_execute()

    # Schedule full analysis every 4 hours
    for t in config.FULL_ANALYSIS_SCHEDULE:
        schedule.every().day.at(t).do(make_decision_and_execute)

    # Quick risk check every 30 minutes
    schedule.every(config.QUICK_RISK_CHECK_INTERVAL_MINUTES).minutes.do(quick_risk_check)

    # DCA pending tranche check
    if config.DCA_ENABLED:
        schedule.every(config.DCA_INTERVAL_MINUTES).minutes.do(check_pending_dca)

    logger.info("Bot started - schedules configured")
    logger.info(f"  Full analysis: {config.FULL_ANALYSIS_SCHEDULE}")
    logger.info(f"  Risk check: every {config.QUICK_RISK_CHECK_INTERVAL_MINUTES}min")

    while not shutdown_requested:
        schedule.run_pending()
        time.sleep(1)

    logger.info("Bot shutdown complete")
