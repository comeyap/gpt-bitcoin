"""Comprehensive unit tests for autotrade_v3.py"""

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, mock_open

import pytest
import pandas as pd
import numpy as np

# Patch external dependencies before importing
with patch.dict(os.environ, {
    "OPENAI_API_KEY": "test-key",
    "UPBIT_ACCESS_KEY": "test-access",
    "UPBIT_SECRET_KEY": "test-secret",
}):
    with patch("pyupbit.Upbit"), patch("openai.OpenAI"):
        import autotrade_v3 as at
        import config
        from trading import (
            utils, database, indicators, market,
            external, orderbook, decision, dca, execution, gpt,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.sqlite")
    database.initialize_db(db_path)
    database.migrate_db(db_path)
    return db_path


@pytest.fixture
def sample_status():
    return json.dumps({
        "current_time": 1700000000000,
        "orderbook": {
            "timestamp": 1700000000000,
            "orderbook_units": [
                {"ask_price": 50000000, "ask_size": 0.5, "bid_price": 49990000, "bid_size": 0.3},
                {"ask_price": 50010000, "ask_size": 1.0, "bid_price": 49980000, "bid_size": 0.5},
                {"ask_price": 50020000, "ask_size": 1.5, "bid_price": 49970000, "bid_size": 0.8},
            ],
        },
        "btc_balance": 0.1,
        "krw_balance": 5000000,
        "btc_avg_buy_price": 48000000,
    })


@pytest.fixture
def sample_market_context():
    return {
        "trend": "up",
        "rsi": 55.0,
        "volatility": 0.025,
        "momentum": 0.02,
        "atr": 500000,
        "vwap": 49500000,
        "current_price": 50000000,
        "support_resistance": {
            "nearest_resistance": 51000000,
            "nearest_support": 48000000,
            "resistance_levels": [51000000],
            "support_levels": [48000000],
        },
        "regime": "trending_up",
        "adx": 30,
    }


@pytest.fixture
def sample_ohlcv_df():
    np.random.seed(42)
    n = 30
    dates = pd.date_range(end=datetime.now(), periods=n, freq="h")
    base_price = 50000000
    close = base_price + np.cumsum(np.random.randn(n) * 100000)
    df = pd.DataFrame({
        "open": close - np.random.rand(n) * 50000,
        "high": close + np.random.rand(n) * 100000,
        "low": close - np.random.rand(n) * 100000,
        "close": close,
        "volume": np.random.rand(n) * 10 + 1,
    }, index=dates)
    return df


@pytest.fixture
def dca_state_file(tmp_path):
    return str(tmp_path / "dca_state.json")


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------
class TestSafeFloat:
    def test_valid_float(self):
        assert utils.safe_float(3.14) == 3.14

    def test_valid_string(self):
        assert utils.safe_float("42.5") == 42.5

    def test_none(self):
        assert utils.safe_float(None) == 0.0

    def test_invalid_string(self):
        assert utils.safe_float("abc") == 0.0

    def test_custom_default(self):
        assert utils.safe_float(None, -1.0) == -1.0

    def test_integer(self):
        assert utils.safe_float(10) == 10.0

    def test_zero(self):
        assert utils.safe_float(0) == 0.0

    def test_empty_string(self):
        assert utils.safe_float("") == 0.0


class TestClampPercentage:
    def test_normal_value(self):
        assert utils.clamp_percentage(30, 50, 70) == 30

    def test_none_uses_default(self):
        assert utils.clamp_percentage(None, 50, 70) == 50

    def test_exceeds_max(self):
        assert utils.clamp_percentage(80, 50, 70) == 70

    def test_negative_clamped_to_zero(self):
        assert utils.clamp_percentage(-10, 50, 70) == 0

    def test_above_100_clamped(self):
        assert utils.clamp_percentage(150, 50, 70) == 70

    def test_invalid_string_uses_default(self):
        assert utils.clamp_percentage("abc", 50, 70) == 50

    def test_zero(self):
        assert utils.clamp_percentage(0, 50, 70) == 0

    def test_string_number(self):
        assert utils.clamp_percentage("40", 50, 70) == 40


class TestAppendReason:
    def test_both_present(self):
        assert utils.append_reason("A", "B") == "A | B"

    def test_empty_reason(self):
        assert utils.append_reason("", "B") == "B"

    def test_empty_note(self):
        assert utils.append_reason("A", "") == "A"

    def test_none_note(self):
        assert utils.append_reason("A", None) == "A"

    def test_none_reason_with_note(self):
        assert utils.append_reason(None, "B") == "B"

    def test_both_empty(self):
        assert utils.append_reason("", "") == ""


# ---------------------------------------------------------------------------
# Database functions
# ---------------------------------------------------------------------------
class TestInitializeDb:
    def test_creates_decisions_table(self, tmp_db):
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'"
            )
            assert cursor.fetchone() is not None

    def test_idempotent(self, tmp_db):
        database.initialize_db(tmp_db)
        database.initialize_db(tmp_db)
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.execute("SELECT count(*) FROM decisions")
            assert cursor.fetchone()[0] == 0


class TestMigrateDb:
    def test_adds_new_columns(self, tmp_db):
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.execute("PRAGMA table_info(decisions)")
            columns = {row[1] for row in cursor.fetchall()}
        assert "high_watermark" in columns
        assert "market_context" in columns

    def test_idempotent(self, tmp_db):
        database.migrate_db(tmp_db)
        database.migrate_db(tmp_db)


class TestSaveDecisionToDb:
    def test_save_buy_decision(self, tmp_db, sample_status):
        with patch.object(config, "DB_PATH", tmp_db):
            d = {
                "decision": "buy", "percentage": 30, "reason": "Test buy",
                "high_watermark": 50000000, "market_context_summary": '{"trend": "up"}',
            }
            database.save_decision_to_db(d, sample_status)
        with sqlite3.connect(tmp_db) as conn:
            row = conn.execute("SELECT * FROM decisions").fetchone()
        assert row is not None
        assert row[2] == "buy"
        assert row[3] == 30

    def test_sell_resets_watermark_on_full_close(self, tmp_db, sample_status):
        with patch.object(config, "DB_PATH", tmp_db):
            d = {
                "decision": "sell", "percentage": 100, "reason": "Full sell",
                "high_watermark": 50000000, "market_context_summary": "",
            }
            database.save_decision_to_db(d, sample_status)
        with sqlite3.connect(tmp_db) as conn:
            row = conn.execute("SELECT high_watermark FROM decisions").fetchone()
        assert row[0] == 0.0


class TestFetchLastDecisions:
    def test_no_decisions(self, tmp_db):
        result = database.fetch_last_decisions(tmp_db)
        assert result == "No decisions found."

    def test_with_decisions(self, tmp_db):
        with sqlite3.connect(tmp_db) as conn:
            conn.execute(
                "INSERT INTO decisions (timestamp, decision, percentage, reason, "
                "btc_balance, krw_balance, btc_avg_buy_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("2024-01-01 12:00:00", "buy", 30, "test", 0.1, 5000000, 48000000),
            )
        result = database.fetch_last_decisions(tmp_db, num=1)
        assert "buy" in result


class TestGetLastDecisionTime:
    def test_no_decisions(self, tmp_db):
        assert database.get_last_decision_time(tmp_db) is None

    def test_with_decision(self, tmp_db):
        with sqlite3.connect(tmp_db) as conn:
            conn.execute(
                "INSERT INTO decisions (timestamp, decision) VALUES (?, ?)",
                ("2024-01-01 12:00:00", "buy"),
            )
        result = database.get_last_decision_time(tmp_db)
        assert isinstance(result, datetime)


class TestGetHighWatermark:
    def test_no_data(self, tmp_db):
        assert database.get_high_watermark(tmp_db) == 0.0

    def test_with_watermark(self, tmp_db):
        with sqlite3.connect(tmp_db) as conn:
            conn.execute(
                "INSERT INTO decisions (timestamp, decision, high_watermark) VALUES (?, ?, ?)",
                ("2024-01-01 12:00:00", "buy", 55000000),
            )
        assert database.get_high_watermark(tmp_db) == 55000000


class TestComputeHighWatermark:
    def test_new_high(self):
        with patch.object(database, "get_high_watermark", return_value=49000000):
            result = database.compute_high_watermark(51000000, 48000000)
        assert result == 51000000

    def test_stored_higher(self):
        with patch.object(database, "get_high_watermark", return_value=52000000):
            result = database.compute_high_watermark(50000000, 48000000)
        assert result == 52000000

    def test_zero_price(self):
        result = database.compute_high_watermark(0, 48000000)
        assert result == 0.0

    def test_zero_avg_price(self):
        result = database.compute_high_watermark(50000000, 0)
        assert result == 0.0


# ---------------------------------------------------------------------------
# Market data functions
# ---------------------------------------------------------------------------
class TestAddIndicators:
    def test_adds_all_indicators(self, sample_ohlcv_df):
        result = indicators.add_indicators(sample_ohlcv_df)
        for col in ["SMA_10", "EMA_10", "RSI_14", "MACD", "Signal_Line",
                     "MACD_Histogram", "Middle_Band", "Upper_Band", "Lower_Band",
                     "ATR_14", "VWAP"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_preserves_original_columns(self, sample_ohlcv_df):
        result = indicators.add_indicators(sample_ohlcv_df)
        for col in ["open", "high", "low", "close", "volume"]:
            assert col in result.columns


class TestDetectSupportResistance:
    def test_returns_dict(self, sample_ohlcv_df):
        result = indicators.detect_support_resistance(sample_ohlcv_df)
        assert "nearest_resistance" in result
        assert "nearest_support" in result
        assert "resistance_levels" in result
        assert "support_levels" in result

    def test_handles_empty_df(self):
        df = pd.DataFrame({"high": [], "low": [], "close": []})
        result = indicators.detect_support_resistance(df)
        assert result["nearest_resistance"] is None


class TestDetectMarketRegime:
    def test_disabled(self, sample_ohlcv_df):
        with patch.object(config, "REGIME_DETECTION_ENABLED", False):
            result = market.detect_market_regime(sample_ohlcv_df)
        assert result == "unknown"

    def test_trending_up(self):
        df = pd.DataFrame({
            f"ADX_{config.ADX_LENGTH}": [30],
            f"DMP_{config.ADX_LENGTH}": [25],
            f"DMN_{config.ADX_LENGTH}": [15],
            "EMA_10": [100],
            "SMA_10": [95],
        })
        result = market.detect_market_regime(df)
        assert result == "trending_up"

    def test_trending_down(self):
        df = pd.DataFrame({
            f"ADX_{config.ADX_LENGTH}": [30],
            f"DMP_{config.ADX_LENGTH}": [15],
            f"DMN_{config.ADX_LENGTH}": [25],
            "EMA_10": [90],
            "SMA_10": [95],
        })
        result = market.detect_market_regime(df)
        assert result == "trending_down"

    def test_ranging(self):
        df = pd.DataFrame({
            f"ADX_{config.ADX_LENGTH}": [20],
            f"DMP_{config.ADX_LENGTH}": [15],
            f"DMN_{config.ADX_LENGTH}": [15],
            "EMA_10": [100],
            "SMA_10": [100],
        })
        result = market.detect_market_regime(df)
        assert result == "ranging"


class TestBuildMarketContext:
    def test_returns_all_keys(self, sample_ohlcv_df):
        df = indicators.add_indicators(sample_ohlcv_df)
        result = market.build_market_context(df)
        expected_keys = {"trend", "rsi", "volatility", "momentum", "atr",
                         "vwap", "current_price", "support_resistance",
                         "regime", "adx"}
        assert expected_keys.issubset(result.keys())


# ---------------------------------------------------------------------------
# External data
# ---------------------------------------------------------------------------
class TestGetNewsData:
    def test_success(self):
        xml = b"""<?xml version="1.0"?>
        <rss><channel>
            <item>
                <title>Bitcoin rises</title>
                <source>Reuters</source>
                <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
            </item>
        </channel></rss>"""
        mock_resp = MagicMock()
        mock_resp.content = xml
        mock_resp.raise_for_status = MagicMock()

        with patch("trading.external.requests.get", return_value=mock_resp):
            result = external.get_news_data()
        assert "Bitcoin rises" in result

    def test_failure(self):
        with patch("trading.external.requests.get", side_effect=Exception("timeout")):
            result = external.get_news_data()
        assert "No news data" in result


class TestFetchFearAndGreedIndex:
    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"value": "50", "classification": "Neutral"}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("trading.external.requests.get", return_value=mock_resp):
            result = external.fetch_fear_and_greed_index()
        assert "50" in result

    def test_failure(self):
        with patch("trading.external.requests.get", side_effect=Exception("error")):
            result = external.fetch_fear_and_greed_index()
        assert "No fear and greed" in result


# ---------------------------------------------------------------------------
# Orderbook depth analysis
# ---------------------------------------------------------------------------
class TestAnalyzeOrderbookDepth:
    def test_normal_order(self):
        ob = {
            "orderbook_units": [
                {"ask_price": 50000000, "ask_size": 1.0},
                {"ask_price": 50010000, "ask_size": 2.0},
            ]
        }
        result = orderbook.analyze_orderbook_depth(ob, 10000000)
        assert result["executable"] is True
        assert result["slippage_pct"] >= 0

    def test_empty_orderbook(self):
        result = orderbook.analyze_orderbook_depth({"orderbook_units": []}, 1000000)
        assert result["slippage_pct"] == 0
        assert result["executable"] is True

    def test_large_order_not_fully_fillable(self):
        ob = {"orderbook_units": [{"ask_price": 50000000, "ask_size": 0.001}]}
        result = orderbook.analyze_orderbook_depth(ob, 1000000000)
        assert result["executable"] is False

    def test_zero_ask_price(self):
        ob = {"orderbook_units": [{"ask_price": 0, "ask_size": 1.0}]}
        result = orderbook.analyze_orderbook_depth(ob, 1000000)
        assert result["executable"] is True


# ---------------------------------------------------------------------------
# GPT analysis
# ---------------------------------------------------------------------------
class TestGetInstructions:
    def test_file_exists(self, tmp_path):
        f = tmp_path / "instructions.md"
        f.write_text("Test instructions")
        result = gpt.get_instructions(str(f))
        assert result == "Test instructions"

    def test_file_missing(self):
        result = gpt.get_instructions("/nonexistent/path.md")
        assert result is None


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------
class TestNormalizeDecision:
    def test_valid_buy(self):
        advice = json.dumps({"decision": "buy", "percentage": 30, "reason": "good"})
        result = decision.normalize_decision(advice)
        assert result["decision"] == "buy"
        assert result["percentage"] == 30

    def test_valid_sell(self):
        advice = json.dumps({"decision": "sell", "percentage": 50, "reason": "taking profit"})
        result = decision.normalize_decision(advice)
        assert result["decision"] == "sell"
        assert result["percentage"] == 50

    def test_hold(self):
        advice = json.dumps({"decision": "hold", "percentage": 0, "reason": "uncertain"})
        result = decision.normalize_decision(advice)
        assert result["decision"] == "hold"
        assert result["percentage"] == 0

    def test_none_advice(self):
        result = decision.normalize_decision(None)
        assert result["decision"] == "hold"

    def test_invalid_json(self):
        result = decision.normalize_decision("not json")
        assert result["decision"] == "hold"

    def test_invalid_decision_value(self):
        advice = json.dumps({"decision": "short", "percentage": 50})
        result = decision.normalize_decision(advice)
        assert result["decision"] == "hold"

    def test_buy_clamped_to_max(self):
        advice = json.dumps({"decision": "buy", "percentage": 100})
        result = decision.normalize_decision(advice)
        assert result["percentage"] <= config.MAX_BUY_PERCENT

    def test_sell_clamped_to_max(self):
        advice = json.dumps({"decision": "sell", "percentage": 100})
        result = decision.normalize_decision(advice)
        assert result["percentage"] <= config.MAX_SELL_PERCENT

    def test_hold_forces_zero_pct(self):
        advice = json.dumps({"decision": "hold", "percentage": 50})
        result = decision.normalize_decision(advice)
        assert result["percentage"] == 0


class TestApplyVolatilityAdjustment:
    def test_high_volatility_reduces(self):
        ctx = {"volatility": 0.05}
        result = decision.apply_volatility_adjustment(50, ctx)
        assert result == 50 * config.VOLATILITY_REDUCTION

    def test_low_volatility_boosts(self):
        ctx = {"volatility": 0.01}
        result = decision.apply_volatility_adjustment(50, ctx)
        assert result == 50 * config.VOLATILITY_BOOST

    def test_normal_volatility_unchanged(self):
        ctx = {"volatility": 0.025}
        result = decision.apply_volatility_adjustment(50, ctx)
        assert result == 50

    def test_zero_volatility(self):
        ctx = {"volatility": 0}
        result = decision.apply_volatility_adjustment(50, ctx)
        assert result == 50


class TestApplyRegimeAdjustment:
    def test_ranging_reduces(self):
        ctx = {"regime": "ranging"}
        result = decision.apply_regime_adjustment("buy", 50, ctx)
        assert result == 50 * config.REGIME_RANGING_SIZE_MULT

    def test_trending_up_buy_boosts(self):
        ctx = {"regime": "trending_up"}
        result = decision.apply_regime_adjustment("buy", 50, ctx)
        assert result == 50 * config.REGIME_TRENDING_SIZE_MULT

    def test_trending_up_sell_reduces(self):
        ctx = {"regime": "trending_up"}
        result = decision.apply_regime_adjustment("sell", 50, ctx)
        assert result == 50 * config.REGIME_COUNTER_TREND_SIZE_MULT

    def test_trending_down_sell_boosts(self):
        ctx = {"regime": "trending_down"}
        result = decision.apply_regime_adjustment("sell", 50, ctx)
        assert result == 50 * config.REGIME_TRENDING_SIZE_MULT

    def test_trending_down_buy_reduces(self):
        ctx = {"regime": "trending_down"}
        result = decision.apply_regime_adjustment("buy", 50, ctx)
        assert result == 50 * config.REGIME_COUNTER_TREND_SIZE_MULT

    def test_unknown_unchanged(self):
        ctx = {"regime": "unknown"}
        result = decision.apply_regime_adjustment("buy", 50, ctx)
        assert result == 50

    def test_disabled(self):
        with patch.object(config, "REGIME_DETECTION_ENABLED", False):
            result = decision.apply_regime_adjustment("buy", 50, {"regime": "ranging"})
        assert result == 50


class TestApplyTieredTakeProfit:
    def test_no_trigger_below_threshold(self):
        result = decision.apply_tiered_take_profit(0.02, -0.01)
        assert result is None

    def test_momentum_weakening_trigger(self):
        result = decision.apply_tiered_take_profit(0.04, -0.01)
        assert result is not None
        assert result["decision"] == "sell"

    def test_momentum_positive_no_trigger_for_first_tier(self):
        result = decision.apply_tiered_take_profit(0.04, 0.01)
        assert result is None

    def test_always_trigger_at_8pct(self):
        result = decision.apply_tiered_take_profit(0.09, 0.05)
        assert result is not None
        assert result["decision"] == "sell"
        assert result["percentage"] == 20

    def test_highest_tier_priority(self):
        result = decision.apply_tiered_take_profit(0.30, 0.0)
        assert result is not None
        assert result["percentage"] == 50  # 25% tier


class TestCheckPositionRisk:
    def test_stop_loss(self):
        with patch.object(config, "STOP_LOSS_PCT", 0.05):
            result = decision.check_position_risk(47000000, 50000000)
        assert result is not None
        assert result["decision"] == "sell"
        assert "Stop-loss" in result["reason"]

    def test_no_risk(self):
        with patch.object(database, "get_high_watermark", return_value=50500000):
            result = decision.check_position_risk(50000000, 49000000)
        assert result is None

    def test_zero_avg_price(self):
        result = decision.check_position_risk(50000000, 0)
        assert result is None

    def test_zero_current_price(self):
        result = decision.check_position_risk(0, 50000000)
        assert result is None

    def test_trailing_stop(self):
        with patch.object(config, "TRAILING_STOP_ENABLED", True), \
             patch.object(config, "TRAILING_STOP_PCT", 0.03), \
             patch.object(database, "get_high_watermark", return_value=52000000):
            result = decision.check_position_risk(49000000, 48000000)
        assert result is not None
        assert "Trailing stop" in result["reason"]

    def test_dynamic_stop_loss_uses_atr(self):
        """ATR-based dynamic stop-loss should widen the stop in volatile markets."""
        ctx = {"atr": 2000000}  # ATR = 2M on a 50M price → 4M stop (8%)
        with patch.object(config, "DYNAMIC_STOP_LOSS_ENABLED", True), \
             patch.object(config, "DYNAMIC_STOP_LOSS_ATR_MULT", 2.0), \
             patch.object(config, "STOP_LOSS_PCT", 0.05):
            # 6% drop should NOT trigger stop (ATR-based threshold = 8%)
            result = decision.check_position_risk(47000000, 50000000, market_context=ctx)
        assert result is None

    def test_dynamic_stop_loss_falls_back_to_fixed(self):
        """When ATR is 0, should fall back to fixed stop-loss."""
        ctx = {"atr": 0}
        with patch.object(config, "DYNAMIC_STOP_LOSS_ENABLED", True), \
             patch.object(config, "STOP_LOSS_PCT", 0.05):
            result = decision.check_position_risk(47000000, 50000000, market_context=ctx)
        assert result is not None
        assert "Stop-loss" in result["reason"]


class TestDynamicStopLoss:
    def test_compute_dynamic_wider_than_fixed(self):
        ctx = {"atr": 3000000}  # ATR=3M, price=50M → 12% dynamic
        with patch.object(config, "DYNAMIC_STOP_LOSS_ENABLED", True), \
             patch.object(config, "DYNAMIC_STOP_LOSS_ATR_MULT", 2.0), \
             patch.object(config, "STOP_LOSS_PCT", 0.05):
            result = decision._compute_dynamic_stop_loss(50000000, ctx)
        assert result == 0.12  # 3M * 2 / 50M

    def test_compute_dynamic_uses_fixed_as_floor(self):
        ctx = {"atr": 500000}  # ATR=500K, price=50M → 2% dynamic < 5% fixed
        with patch.object(config, "DYNAMIC_STOP_LOSS_ENABLED", True), \
             patch.object(config, "DYNAMIC_STOP_LOSS_ATR_MULT", 2.0), \
             patch.object(config, "STOP_LOSS_PCT", 0.05):
            result = decision._compute_dynamic_stop_loss(50000000, ctx)
        assert result == 0.05  # fixed floor


class TestRSIOversoldAccumulation:
    def test_deep_oversold_boosts_buy(self):
        with patch.object(config, "RSI_OVERSOLD_ACCUMULATION_ENABLED", True), \
             patch.object(config, "RSI_DEEP_OVERSOLD", 25), \
             patch.object(config, "RSI_OVERSOLD_BOOST", 1.5):
            pct, reason = decision._apply_buy_filters(30, "", "up", 20, 0.01)
        assert pct == 30 * 1.5
        assert "deep oversold" in reason

    def test_normal_rsi_no_boost(self):
        with patch.object(config, "RSI_OVERSOLD_ACCUMULATION_ENABLED", True), \
             patch.object(config, "RSI_DEEP_OVERSOLD", 25):
            pct, reason = decision._apply_buy_filters(30, "", "up", 50, 0.01)
        assert pct == 30
        assert "deep oversold" not in reason


class TestTrendingCooldown:
    def test_shorter_cooldown_in_trend(self, sample_status):
        ctx = {"trend": "up", "rsi": 55, "momentum": 0.02, "volatility": 0.02,
               "regime": "trending_up"}
        with patch.object(config, "MIN_TRADE_INTERVAL_TRENDING", 15), \
             patch.object(config, "MIN_TRADE_INTERVAL_MINUTES", 30), \
             patch.object(decision, "get_last_decision_time",
                          return_value=datetime.now() - timedelta(minutes=20)), \
             patch.object(decision, "check_position_risk", return_value=None):
            d = {"decision": "buy", "percentage": 30, "reason": "test"}
            result = decision.apply_risk_policy(d, sample_status, ctx)
        # 20 mins > 15 min trending cooldown → should NOT be in cooldown
        assert result["decision"] != "hold" or "Cooldown" not in result.get("reason", "")


class TestApplyRiskPolicy:
    def test_cooldown(self, sample_status, sample_market_context, tmp_db):
        with patch.object(config, "DB_PATH", tmp_db), \
             patch.object(decision, "get_last_decision_time",
                          return_value=datetime.now() - timedelta(minutes=5)):
            d = {"decision": "buy", "percentage": 30, "reason": "test"}
            result = decision.apply_risk_policy(d, sample_status, sample_market_context)
        assert result["decision"] == "hold"
        assert "Cooldown" in result["reason"]

    def test_insufficient_krw(self, sample_market_context):
        status = json.dumps({
            "orderbook": {"orderbook_units": [{"ask_price": 50000000, "ask_size": 1}]},
            "btc_balance": 0, "krw_balance": 1000, "btc_avg_buy_price": 0,
        })
        with patch.object(decision, "get_last_decision_time", return_value=None):
            d = {"decision": "buy", "percentage": 30, "reason": "test"}
            result = decision.apply_risk_policy(d, status, sample_market_context)
        assert result["decision"] == "hold"
        assert "Insufficient" in result["reason"]

    def test_no_btc_to_sell(self, sample_market_context):
        status = json.dumps({
            "orderbook": {"orderbook_units": [{"ask_price": 50000000, "ask_size": 1}]},
            "btc_balance": 0, "krw_balance": 10000000, "btc_avg_buy_price": 0,
        })
        with patch.object(decision, "get_last_decision_time", return_value=None):
            d = {"decision": "sell", "percentage": 50, "reason": "test"}
            result = decision.apply_risk_policy(d, status, sample_market_context)
        assert result["decision"] == "hold"
        assert "No BTC" in result["reason"]

    def test_buy_filters_applied(self, sample_status):
        ctx = {
            "trend": "down", "rsi": 75, "momentum": -0.02,
            "volatility": 0.025, "regime": "ranging",
        }
        with patch.object(decision, "get_last_decision_time", return_value=None), \
             patch.object(decision, "check_position_risk", return_value=None):
            d = {"decision": "buy", "percentage": 50, "reason": "test"}
            result = decision.apply_risk_policy(d, sample_status, ctx)
        assert result["percentage"] < 50
        assert "Downtrend" in result["reason"]

    def test_hold_passes_through(self, sample_market_context):
        status = json.dumps({
            "orderbook": {"orderbook_units": [{"ask_price": 50000000, "ask_size": 1}]},
            "btc_balance": 0, "krw_balance": 5000000, "btc_avg_buy_price": 0,
        })
        with patch.object(decision, "get_last_decision_time", return_value=None):
            d = {"decision": "hold", "percentage": 0, "reason": "wait"}
            result = decision.apply_risk_policy(d, status, sample_market_context)
        assert result["decision"] == "hold"

    def test_min_buy_floor_applied(self, sample_status):
        ctx = {
            "trend": "down", "rsi": 75, "momentum": -0.05,
            "volatility": 0.05, "regime": "ranging",
        }
        with patch.object(decision, "get_last_decision_time", return_value=None), \
             patch.object(decision, "check_position_risk", return_value=None):
            d = {"decision": "buy", "percentage": 20, "reason": "test"}
            result = decision.apply_risk_policy(d, sample_status, ctx)
        if result["decision"] == "buy":
            assert result["percentage"] >= config.MIN_BUY_PCT_FLOOR


# ---------------------------------------------------------------------------
# DCA functions
# ---------------------------------------------------------------------------
class TestLoadSaveDcaState:
    def test_load_missing_file(self, dca_state_file):
        with patch.object(config, "DCA_STATE_FILE", dca_state_file):
            state = dca.load_dca_state()
        assert state["active"] is False
        assert state["tranches_remaining"] == 0

    def test_save_and_load(self, dca_state_file):
        with patch.object(config, "DCA_STATE_FILE", dca_state_file):
            state = {"active": True, "tranches_remaining": 2,
                     "original_percentage": 30, "last_tranche_time": None}
            dca.save_dca_state(state)
            loaded = dca.load_dca_state()
        assert loaded["active"] is True
        assert loaded["tranches_remaining"] == 2


class TestApplyDca:
    def test_disabled(self, dca_state_file):
        with patch.object(config, "DCA_ENABLED", False):
            d = {"decision": "buy", "percentage": 30, "reason": "test"}
            result = dca.apply_dca(d)
        assert result["percentage"] == 30

    def test_non_buy_cancels_active_dca(self, dca_state_file):
        with patch.object(config, "DCA_STATE_FILE", dca_state_file), \
             patch.object(config, "DCA_ENABLED", True):
            dca.save_dca_state({"active": True, "tranches_remaining": 2,
                                "original_percentage": 30, "last_tranche_time": None})
            d = {"decision": "sell", "percentage": 50, "reason": "sell now"}
            result = dca.apply_dca(d)
        assert "DCA cancelled" in result["reason"]

    def test_new_dca_sequence(self, dca_state_file):
        with patch.object(config, "DCA_STATE_FILE", dca_state_file), \
             patch.object(config, "DCA_ENABLED", True), \
             patch.object(config, "DCA_SPLITS", 3):
            d = {"decision": "buy", "percentage": 30, "reason": "buy signal"}
            result = dca.apply_dca(d)
        assert result["percentage"] == 10
        assert "DCA tranche 1/3" in result["reason"]

    def test_hold_during_active_dca(self, dca_state_file):
        with patch.object(config, "DCA_STATE_FILE", dca_state_file), \
             patch.object(config, "DCA_ENABLED", True):
            dca.save_dca_state({"active": True, "tranches_remaining": 2,
                                "original_percentage": 30, "last_tranche_time": None})
            d = {"decision": "buy", "percentage": 30, "reason": "another buy"}
            result = dca.apply_dca(d)
        assert result["decision"] == "hold"
        assert "DCA already active" in result["reason"]


# ---------------------------------------------------------------------------
# Execution functions
# ---------------------------------------------------------------------------
class TestExecuteBuy:
    def test_buy_above_minimum(self):
        mock_upbit = MagicMock()
        mock_upbit.get_balance.return_value = 10000000
        mock_upbit.buy_market_order.return_value = {"uuid": "test"}
        execution.upbit = mock_upbit
        execution.execute_buy(30)
        mock_upbit.buy_market_order.assert_called_once()

    def test_buy_below_minimum(self):
        mock_upbit = MagicMock()
        mock_upbit.get_balance.return_value = 1000
        execution.upbit = mock_upbit
        execution.execute_buy(30)
        mock_upbit.buy_market_order.assert_not_called()


class TestExecuteSell:
    def test_sell_above_minimum(self):
        mock_upbit = MagicMock()
        mock_upbit.get_balance.return_value = 0.1
        mock_upbit.sell_market_order.return_value = {"uuid": "test"}
        execution.upbit = mock_upbit
        mock_orderbook = {"orderbook_units": [{"ask_price": 50000000}]}
        with patch("trading.execution.pyupbit.get_orderbook", return_value=mock_orderbook):
            execution.execute_sell(50)
        mock_upbit.sell_market_order.assert_called_once()

    def test_sell_below_minimum(self):
        mock_upbit = MagicMock()
        mock_upbit.get_balance.return_value = 0.00001
        execution.upbit = mock_upbit
        mock_orderbook = {"orderbook_units": [{"ask_price": 50000000}]}
        with patch("trading.execution.pyupbit.get_orderbook", return_value=mock_orderbook):
            execution.execute_sell(50)
        mock_upbit.sell_market_order.assert_not_called()


# ---------------------------------------------------------------------------
# Chart generation
# ---------------------------------------------------------------------------
class TestGenerateChartImage:
    def test_generates_base64(self, sample_ohlcv_df):
        df = indicators.add_indicators(sample_ohlcv_df)
        result = market.generate_chart_image(df)
        assert isinstance(result, str)
        if result:
            import base64
            base64.b64decode(result)


# ---------------------------------------------------------------------------
# Integration-style tests for full flow
# ---------------------------------------------------------------------------
class TestMakeDecisionAndExecute:
    @patch.object(at, "save_decision_to_db")
    @patch.object(at, "execute_buy")
    @patch.object(at, "execute_sell")
    @patch.object(at, "apply_dca", side_effect=lambda d: d)
    @patch.object(at, "apply_risk_policy")
    @patch.object(at, "normalize_decision")
    @patch.object(at, "analyze_data_with_gpt4", return_value='{"decision":"buy","percentage":30,"reason":"test"}')
    @patch.object(at, "generate_chart_image", return_value="")
    @patch.object(at, "get_current_status")
    @patch.object(at, "fetch_fear_and_greed_index", return_value="50")
    @patch.object(at, "fetch_last_decisions", return_value="No decisions")
    @patch.object(at, "fetch_and_prepare_data")
    @patch.object(at, "get_news_data", return_value="news")
    def test_buy_flow(self, mock_news, mock_data, mock_last, mock_fng,
                      mock_status, mock_chart, mock_gpt, mock_norm,
                      mock_risk, mock_dca, mock_sell, mock_buy, mock_save,
                      sample_ohlcv_df, sample_market_context):
        mock_data.return_value = ("data", sample_market_context, sample_ohlcv_df)
        mock_status.return_value = json.dumps({
            "orderbook": {"orderbook_units": [{"ask_price": 50000000, "ask_size": 1}]},
            "btc_balance": 0.1, "krw_balance": 5000000, "btc_avg_buy_price": 48000000,
        })
        mock_norm.return_value = {"decision": "buy", "percentage": 30, "reason": "test"}
        mock_risk.return_value = {"decision": "buy", "percentage": 30, "reason": "test"}

        at.make_decision_and_execute()

        mock_buy.assert_called_once_with(30)
        mock_sell.assert_not_called()


class TestQuickRiskCheck:
    @patch.object(at, "save_decision_to_db")
    @patch.object(at, "execute_sell")
    @patch("autotrade_v3.pyupbit.get_ohlcv")
    @patch.object(at, "get_current_status")
    def test_no_position(self, mock_status, mock_ohlcv, mock_sell, mock_save):
        mock_status.return_value = json.dumps({
            "orderbook": {"orderbook_units": [{"ask_price": 50000000, "ask_size": 1}]},
            "btc_balance": 0, "krw_balance": 5000000, "btc_avg_buy_price": 0,
        })
        at.quick_risk_check()
        mock_sell.assert_not_called()

    @patch.object(at, "save_decision_to_db")
    @patch.object(at, "execute_sell")
    @patch("autotrade_v3.pyupbit.get_ohlcv")
    @patch.object(at, "get_current_status")
    def test_stop_loss_triggered(self, mock_status, mock_ohlcv, mock_sell, mock_save):
        mock_status.return_value = json.dumps({
            "orderbook": {"orderbook_units": [{"ask_price": 45000000, "ask_size": 1}]},
            "btc_balance": 0.1, "krw_balance": 5000000, "btc_avg_buy_price": 50000000,
        })
        mock_ohlcv.return_value = pd.DataFrame({
            "close": [46000000, 45800000, 45600000, 45400000, 45200000, 45000000]
        })
        at.quick_risk_check()
        mock_sell.assert_called_once()


# ---------------------------------------------------------------------------
# Backward compatibility tests
# ---------------------------------------------------------------------------
class TestBackwardCompatibility:
    """Ensure backtest.py imports still work through autotrade_v3."""

    def test_safe_float_accessible(self):
        assert at.safe_float is utils.safe_float

    def test_clamp_percentage_accessible(self):
        assert at.clamp_percentage is utils.clamp_percentage

    def test_add_indicators_accessible(self):
        assert at.add_indicators is indicators.add_indicators

    def test_apply_tiered_take_profit_accessible(self):
        assert at.apply_tiered_take_profit is decision.apply_tiered_take_profit

    def test_apply_volatility_adjustment_accessible(self):
        assert at.apply_volatility_adjustment is decision.apply_volatility_adjustment

    def test_apply_regime_adjustment_accessible(self):
        assert at.apply_regime_adjustment is decision.apply_regime_adjustment
