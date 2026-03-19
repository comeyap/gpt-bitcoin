"""Trade execution: buy and sell market orders."""

import logging

import pyupbit

import config
from trading.utils import safe_float

logger = logging.getLogger("autotrade")

# Lazy init — set from autotrade_v3 main
upbit = None


def set_upbit(upbit_instance):
    global upbit
    upbit = upbit_instance


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
