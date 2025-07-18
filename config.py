# Configuration settings for the trading bot

# Trading settings
MIN_ORDER_AMOUNT = 5000  # 최소 주문 금액 (KRW)
FEE_RATE = 0.9995  # 수수료율 (0.05% 수수료)

# API timeouts
API_TIMEOUT = 10  # API 호출 타임아웃 (초)
WEBDRIVER_TIMEOUT = 20  # WebDriver 타임아웃 (초)

# Chart settings
CHART_WINDOW_SIZE = "1920x1080"
SCREENSHOT_PATH = "./screenshot.png"

# Database settings
DB_PATH = 'trading_decisions.sqlite'
DEFAULT_DECISIONS_LIMIT = 10

# Schedule settings
TRADING_SCHEDULE = [
    "00:01",  # 자정
    "08:01",  # 오전 8시
    "16:01"   # 오후 4시
]

# Retry settings
MAX_RETRIES = 5
RETRY_DELAY_SECONDS = 5

# Fear and Greed Index settings
FEAR_GREED_LIMIT = 30
