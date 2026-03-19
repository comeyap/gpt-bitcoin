"""Dollar Cost Averaging (DCA) logic for splitting buy orders."""

import json
import logging
from datetime import datetime

import config
from trading.utils import safe_float, append_reason
from trading.database import compute_high_watermark, save_decision_to_db
from trading.execution import execute_buy

logger = logging.getLogger("autotrade")


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
    """Split buy decisions into multiple tranches via DCA."""
    if not config.DCA_ENABLED:
        return decision
    if decision.get("decision") != "buy":
        state = load_dca_state()
        if state.get("active"):
            state["active"] = False
            state["tranches_remaining"] = 0
            save_dca_state(state)
            decision["reason"] = append_reason(decision.get("reason", ""), "DCA cancelled")
        return decision

    state = load_dca_state()

    if state.get("active") and state.get("tranches_remaining", 0) > 0:
        return {"decision": "hold", "percentage": 0,
                "reason": "DCA already active — pending tranches handled separately",
                "_skip_save": True}

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
    from trading.market import get_current_status

    state = load_dca_state()
    if not state.get("active") or state.get("tranches_remaining", 0) <= 0:
        return

    last_time = state.get("last_tranche_time")
    if last_time:
        elapsed = (datetime.now() - datetime.fromisoformat(last_time)).total_seconds() / 60
        if elapsed < config.DCA_INTERVAL_MINUTES:
            logger.info(f"DCA waiting ({elapsed:.0f}m / {config.DCA_INTERVAL_MINUTES}m)")
            return

    try:
        current_status = get_current_status()
        status = json.loads(current_status)
        units = status.get("orderbook", {}).get("orderbook_units", [])
        price = safe_float(units[0].get("ask_price") if units else 0)
        avg = safe_float(status.get("btc_avg_buy_price"))
        krw = safe_float(status.get("krw_balance"))

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
