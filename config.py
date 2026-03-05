# Configuration settings for the trading bot

import os

# Trading settings
MIN_ORDER_AMOUNT = 5000  # 최소 주문 금액 (KRW)
FEE_RATE = 0.9995  # 수수료율 (0.05% 수수료)
MAX_BUY_PERCENT = 50  # 단일 매수 최대 비율
MAX_SELL_PERCENT = 70  # 단일 매도 최대 비율

# Risk management
STOP_LOSS_PCT = 0.05  # 평균 매수가 대비 5% 하락 시 손절
STOP_LOSS_SELL_PCT = 100  # 손절 시 매도 비율
MIN_TRADE_INTERVAL_MINUTES = 30  # 연속 거래 최소 간격

# Tiered take-profit settings
TIERED_TAKE_PROFIT = [
    {"threshold": 0.05, "sell_pct": 20, "condition": "momentum_weakening"},
    {"threshold": 0.10, "sell_pct": 30, "condition": "always"},
    {"threshold": 0.15, "sell_pct": 50, "condition": "always"},
]

# Orderbook depth analysis
MAX_SLIPPAGE_PCT = 0.005  # 0.5% 이상 슬리피지 시 포지션 축소

# API timeouts
API_TIMEOUT = 10  # API 호출 타임아웃 (초)

# Chart settings
SCREENSHOT_PATH = "./chart.png"

# OpenAI settings
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

# Database settings
DB_PATH = 'trading_decisions.sqlite'
DEFAULT_DECISIONS_LIMIT = 10

# Schedule settings
FULL_ANALYSIS_SCHEDULE = [
    "00:01", "02:01", "04:01", "06:01", "08:01", "10:01",
    "12:01", "14:01", "16:01", "18:01", "20:01", "22:01",
]
QUICK_RISK_CHECK_INTERVAL_MINUTES = 30

# Retry settings
MAX_RETRIES = 5
RETRY_DELAY_SECONDS = 5

# Fear and Greed Index settings
FEAR_GREED_LIMIT = 30

# Volatility-based sizing
HIGH_VOLATILITY_THRESHOLD = 0.04
LOW_VOLATILITY_THRESHOLD = 0.015
VOLATILITY_REDUCTION = 0.6
VOLATILITY_BOOST = 1.1

# Trailing stop
TRAILING_STOP_ENABLED = True
TRAILING_STOP_PCT = 0.03        # 고점 대비 3% 하락 시 발동
TRAILING_STOP_SELL_PCT = 70     # 70% 매도 (급격한 전량 청산 방지)

# Market regime detection
REGIME_DETECTION_ENABLED = True
ADX_LENGTH = 14
ADX_TRENDING_THRESHOLD = 25      # ADX > 25 = 추세장
REGIME_RANGING_SIZE_MULT = 0.5   # 횡보장: 포지션 50% 축소
REGIME_TRENDING_SIZE_MULT = 1.3  # 추세 방향 매매: 30% 확대
REGIME_COUNTER_TREND_SIZE_MULT = 0.3  # 역추세 매매: 70% 축소

# DCA settings
DCA_ENABLED = True
DCA_SPLITS = 3              # 매수를 3회로 분할
DCA_INTERVAL_MINUTES = 60   # 분할 간격 (분)
DCA_STATE_FILE = "dca_state.json"

# Backtesting
BACKTEST_DAYS = 180
BACKTEST_INITIAL_KRW = 10_000_000

# Logging
LOG_FILE = 'autotrade.log'
LOG_LEVEL = 'INFO'
