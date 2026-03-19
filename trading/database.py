"""Database operations for persisting trading decisions."""

import json
import logging
import sqlite3
from datetime import datetime

import config
from trading.utils import safe_float

logger = logging.getLogger("autotrade")


def initialize_db(db_path=None):
    if db_path is None:
        db_path = config.DB_PATH
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


def migrate_db(db_path=None):
    if db_path is None:
        db_path = config.DB_PATH
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


def fetch_last_decisions(db_path=None, num=None):
    if db_path is None:
        db_path = config.DB_PATH
    if num is None:
        num = config.DEFAULT_DECISIONS_LIMIT
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


def get_last_decision_time(db_path=None):
    if db_path is None:
        db_path = config.DB_PATH
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


def get_high_watermark(db_path=None):
    if db_path is None:
        db_path = config.DB_PATH
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
    if avg_buy_price <= 0 or current_price <= 0:
        return 0.0
    stored_hw = get_high_watermark()
    return max(stored_hw, avg_buy_price, current_price)
