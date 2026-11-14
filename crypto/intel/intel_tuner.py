# ============================================================
# ARGUS - INTEL TUNER v2.1 (FIXED WINRATE & DYNAMIC WEIGHTS)
# Path: crypto/intel/intel_tuner.py
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

# Настройки для весов и стопов
MAX_BREAKEVENS = 2         
TRAILING_STEP = 0.2        
ATR_STEP = 0.5             
WEIGHT_STEP = 0.05         # Шаг изменения весов индикаторов
MIN_WEIGHT = 0.5
MAX_WEIGHT = 2.0

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
        log.info(f"✅ Конфиг успешно обновлен: {path}")
    except Exception as e:
        log.error(f"❌ Ошибка записи {path}: {e}")
        if temp_path.exists(): temp_path.unlink()

def analyze_deals_and_tune():
    log.info("🧠 Тюнер запущен. Анализ сделок...")
    
    deals = load_json(DEALS_FILE, [])
    config = load_json(CONFIG_FILE, None)
    
    if not config:
        log.error("Конфиг bot_config.json не найден или пуст.")
        return

    now = datetime.now(timezone.utc)
    cutoff_time = now - timedelta(hours=LOOKBACK_HOURS)

    recent_intraday = []
    recent_swing = []
    
    for d in deals:
        if "close_time" not in d or "mode" not in d: continue
        try:
            close_time = datetime.fromisoformat(d["close_time"])
            if close_time.tzinfo is None: close_time = close_time.replace(tzinfo=timezone.utc)
            if close_time >= cutoff_time:
                if d["mode"] == "intraday": recent_intraday.append(d)
                elif d["mode"] == "swing": recent_swing.append(d)
        except ValueError: continue

    _tune_mode("intraday", recent_intraday, config)
    _tune_mode("swing", recent_swing, config)

    config["updated_at"] = now.isoformat()
    save_json_atomic(CONFIG_FILE, config)

def _tune_mode(mode_name, deals, config):
    if len(deals) < MIN_TRADES_TO_TUNE:
        log.info(f"[{mode_name.upper()}] Мало сделок за 24ч ({len(deals)}). Настройки не меняем.")
        return

    wins = sum(1 for d in deals if d.get("pnl_usd", 0) > 0)
    losses = sum(1 for d in deals if d.get("pnl_usd", 0) < 0)
    breakevens = sum(1 for d in deals if d.get("pnl_usd", 0) == 0)
    
    total_trades = len(deals)
    # Считаем винрейт от ОБЩЕГО количества сделок, а не только от результативных
    winrate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    cfg_mode = config[mode_name]
    current_thresh = cfg_mode.get("threshold", 2.5)
    current_act = cfg_mode.get("trailing_activation_pct", 0.5)
    current_atr = cfg_mode.get("trailing_atr_multiplier", 1.5)
    
    log.info(f"[{mode_name.upper()}] Сделок: {total_trades} (W:{wins} L:{losses} B:{breakevens}) | Общий Winrate: {winrate:.1f}%")

    # 1. ДИНАМИЧЕСКОЕ ОБНОВЛЕНИЕ ВЕСОВ
    for deal in deals:
        pnl = deal.get("pnl_usd", 0)
        active = deal.get("active_signals", [])
        
        # Если профит - хвалим индикаторы. Если лосс - наказываем. (Безубыток не трогаем)
        if pnl > 0: direction = 1
        elif pnl < 0: direction = -1
        else: direction = 0
        
        if direction != 0:
            for sig in active:
                if sig in cfg_mode["weights"]:
                    old_w = cfg_mode["weights"][sig]
                    new_w = old_w + (WEIGHT_STEP * direction)
                    # Ограничиваем веса рамками, чтобы не уйти в крайности
                    cfg_mode["weights"][sig] = round(max(MIN_WEIGHT, min(MAX_WEIGHT, new_w)), 2)

    # 2. ЛОГИКА ПОРОГА (THRESHOLD)
    if winrate < 40.0:
        new_thresh = min(MAX_THRESHOLD, current_thresh + THRESHOLD_STEP)
        log.info(f"🔻 Винрейт < 40%. Поднимаем порог с {current_thresh} до {new_thresh}")
        cfg_mode["threshold"] = round(new_thresh, 2)
    elif winrate > 65.0:
        new_thresh = max(MIN_THRESHOLD, current_thresh - THRESHOLD_STEP)
        log.info(f"🔺 Винрейт > 65%. Снижаем порог с {current_thresh} до {new_thresh}")
        cfg_mode["threshold"] = round(new_thresh, 2)

    # 3. ЛОГИКА БЕЗУБЫТКОВ (ЕСЛИ СТОП СЛИШКОМ ТЕСНЫЙ)
    if breakevens > MAX_BREAKEVENS:
        new_act = current_act + TRAILING_STEP
        new_atr = current_atr + ATR_STEP
        
        log.info(f"⚖️ Слишком много безубытков ({breakevens}). Расширяем трейлинг!")
        log.info(f"   Активация: {current_act} -> {new_act}")
        log.info(f"   ATR множитель: {current_atr} -> {new_atr}")
        
        cfg_mode["trailing_activation_pct"] = round(new_act, 2)
        cfg_mode["trailing_atr_multiplier"] = round(new_atr, 2)

if __name__ == "__main__":
    analyze_deals_and_tune()