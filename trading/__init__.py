"""Trading package — modular components for the Bitcoin trading bot."""

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
