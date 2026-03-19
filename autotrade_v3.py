"""Bitcoin auto-trading bot — entry point and backward-compatible re-exports.

The core logic lives in the `trading/` package, split by responsibility:
  trading/utils.py       — safe_float, clamp_percentage, append_reason
  trading/database.py    — SQLite persistence
  trading/indicators.py  — technical indicators, support/resistance
  trading/market.py      — market data, regime detection, charts
  trading/external.py    — news, Fear & Greed index
  trading/orderbook.py   — orderbook depth / slippage
  trading/decision.py    — normalize, risk policy, position sizing
  trading/dca.py         — DCA splitting
  trading/execution.py   — buy/sell order execution
  trading/gpt.py         — GPT analysis
"""

import os
import sys
import signal
import logging
import json
import time
from dotenv import load_dotenv
load_dotenv()

import pyupbit
from openai import OpenAI

import config

# ---------------------------------------------------------------------------
# Re-export everything from trading package for backward compatibility
# (backtest.py imports from autotrade_v3)
# ---------------------------------------------------------------------------
from trading.utils import safe_float, clamp_percentage, append_reason
from trading.database import (
    initialize_db, migrate_db, save_decision_to_db,
    fetch_last_decisions, get_last_decision_time,
    get_high_watermark, compute_high_watermark,
)
from trading.indicators import add_indicators, detect_support_resistance
from trading.market import (
    detect_market_regime, build_market_context,
    fetch_and_prepare_data, generate_chart_image, get_current_status,
)
from trading.external import get_news_data, fetch_fear_and_greed_index
from trading.orderbook import analyze_orderbook_depth
from trading.decision import (
    normalize_decision, apply_volatility_adjustment,
    apply_regime_adjustment, apply_tiered_take_profit,
    apply_risk_policy, check_position_risk,
)
from trading.dca import load_dca_state, save_dca_state, apply_dca, execute_dca_tranche, check_pending_dca
from trading.execution import execute_buy, execute_sell
from trading.gpt import get_instructions, analyze_data_with_gpt4

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


def _init_modules():
    """Wire up lazy-initialized clients to sub-modules."""
    from trading import market as _market
    from trading import execution as _execution
    from trading import gpt as _gpt
    _market.set_upbit(upbit)
    _execution.set_upbit(upbit)
    _gpt.set_client(client)


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

    decision = apply_dca(decision)

    decision["market_context_summary"] = json.dumps({
        "trend": market_ctx.get("trend"),
        "rsi": market_ctx.get("rsi"),
        "momentum": market_ctx.get("momentum"),
        "regime": market_ctx.get("regime"),
        "adx": market_ctx.get("adx"),
    })

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
    import schedule

    setup_logging()
    validate_config()
    _init_modules()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    initialize_db()
    migrate_db()

    make_decision_and_execute()

    for t in config.FULL_ANALYSIS_SCHEDULE:
        schedule.every().day.at(t).do(make_decision_and_execute)

    schedule.every(config.QUICK_RISK_CHECK_INTERVAL_MINUTES).minutes.do(quick_risk_check)

    if config.DCA_ENABLED:
        schedule.every(config.DCA_INTERVAL_MINUTES).minutes.do(check_pending_dca)

    logger.info("Bot started - schedules configured")
    logger.info(f"  Full analysis: {config.FULL_ANALYSIS_SCHEDULE}")
    logger.info(f"  Risk check: every {config.QUICK_RISK_CHECK_INTERVAL_MINUTES}min")

    while not shutdown_requested:
        schedule.run_pending()
        time.sleep(1)

    logger.info("Bot shutdown complete")
