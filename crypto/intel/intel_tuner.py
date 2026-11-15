# ============================================================
# ARGUS - INTEL TUNER v2.2
# Path: crypto/intel/intel_tuner.py
#
# ИЗМЕНЕНИЯ v2.2:
#   - Потолки трейлинга: MAX_TRAILING_ACTIVATION=3.0, MAX_TRAILING_ATR=5.0
#   - Не расширяем трейлинг, если уже на потолке
#   - Лог текущих значений трейлинга при каждом прогоне
# ============================================================

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("intel.tuner")

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DEALS_FILE = CRYPTO_ROOT / "data" / "signals" / "closed_deals.json"
CONFIG_FILE = CRYPTO_ROOT / "data" / "intel" / "bot_config.json"

LOOKBACK_HOURS = 24
MIN_TRADES_TO_TUNE = 3
THRESHOLD_STEP = 0.1
MIN_THRESHOLD = 1.5
MAX_THRESHOLD = 5.0
MAX_BREAKEVENS = 2
TRAILING_STEP = 0.2
ATR_STEP = 0.5
WEIGHT_STEP = 0.05
MIN_WEIGHT = 0.5
MAX_WEIGHT = 2.0
# ── НОВОЕ: потолки трейлинга ────────────────────────────────
MAX_TRAILING_ACTIVATION = 3.0
MAX_TRAILING_ATR = 5.0

def load_json(path, default=None):
    if not path.exists(): return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception as e:
        log.error(f"Ошибка чтения {path}: {e}")
        return default if default is not None else {}

def save_json_atomic(path, data):
    temp_path = path.with_suffix('.tmp')
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
        log.info(f"✅ Конфиг обновлен: {path}")
    except Exception as e:
        log.error(f"❌ Ошибка записи {path}: {e}")
        if temp_path.exists(): temp_path.unlink()

def analyze_deals_and_tune():
    log.info("🧠 Тюнер запущен")
    deals = load_json(DEALS_FILE, [])
    config = load_json(CONFIG_FILE, None)
    if not config:
        return log.error("bot_config.json не найден")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    recent = {"intraday": [], "swing": []}
    for d in deals:
        if "close_time" not in d or "mode" not in d: continue
        try:
            ct = datetime.fromisoformat(d["close_time"])
            if ct.tzinfo is None: ct = ct.replace(tzinfo=timezone.utc)
            if ct >= cutoff and d["mode"] in recent:
                recent[d["mode"]].append(d)
        except ValueError: continue

    for mode in ["intraday", "swing"]:
        _tune_mode(mode, recent[mode], config)

    config["updated_at"] = now.isoformat()
    save_json_atomic(CONFIG_FILE, config)

def _tune_mode(mode_name, deals, config):
    if len(deals) < MIN_TRADES_TO_TUNE:
        log.info(f"[{mode_name.upper()}] Мало сделок ({len(deals)}). Пропуск.")
        return
    wins = sum(1 for d in deals if d.get("pnl_usd", 0) > 0)
    losses = sum(1 for d in deals if d.get("pnl_usd", 0) < 0)
    breakevens = sum(1 for d in deals if d.get("pnl_usd", 0) == 0)
    total = len(deals)
    winrate = (wins / total * 100) if total > 0 else 0
    cfg = config[mode_name]
    thresh = cfg.get("threshold", 2.5)
    act = cfg.get("trailing_activation_pct", 1.0)
    atr = cfg.get("trailing_atr_multiplier", 2.0)
    log.info(f"[{mode_name.upper()}] Сделок: {total} (W:{wins} L:{losses} B:{breakevens}) | Winrate: {winrate:.1f}% | Trail act={act} atr={atr}")

    # 1. Веса
    for deal in deals:
        pnl = deal.get("pnl_usd", 0)
        if pnl == 0: continue
        direction = 1 if pnl > 0 else -1
        for sig in deal.get("active_signals", []):
            if sig in cfg.get("weights", {}):
                old_w = cfg["weights"][sig]
                new_w = old_w + WEIGHT_STEP * direction
                cfg["weights"][sig] = round(max(MIN_WEIGHT, min(MAX_WEIGHT, new_w)), 2)

    # 2. Threshold
    if winrate < 40.0:
        new_t = min(MAX_THRESHOLD, thresh + THRESHOLD_STEP)
        log.info(f"🔻 Winrate<40%. Threshold {thresh}->{new_t}")
        cfg["threshold"] = round(new_t, 2)
    elif winrate > 65.0:
        new_t = max(MIN_THRESHOLD, thresh - THRESHOLD_STEP)
        log.info(f"🔺 Winrate>65%. Threshold {thresh}->{new_t}")
        cfg["threshold"] = round(new_t, 2)

    # 3. Трейлинг с потолками
    if breakevens > MAX_BREAKEVENS:
        if act < MAX_TRAILING_ACTIVATION:
            new_act = min(MAX_TRAILING_ACTIVATION, act + TRAILING_STEP)
            cfg["trailing_activation_pct"] = round(new_act, 2)
            log.info(f"⚖️ Breakevens>{MAX_BREAKEVENS}. Trail act {act}->{new_act}")
        else:
            log.info(f"⚖️ Breakevens>{MAX_BREAKEVENS}, но trail act на потолке ({act})")
        if atr < MAX_TRAILING_ATR:
            new_atr = min(MAX_TRAILING_ATR, atr + ATR_STEP)
            cfg["trailing_atr_multiplier"] = round(new_atr, 2)
            log.info(f"   ATR mult {atr}->{new_atr}")
        else:
            log.info(f"   ATR mult на потолке ({atr})")

if __name__ == "__main__":
    analyze_deals_and_tune()