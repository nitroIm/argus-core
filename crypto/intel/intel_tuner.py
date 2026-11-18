# ============================================================
# ARGUS - INTEL TUNER v3.0 (Self-Adaptive)
# Path: crypto/intel/intel_tuner.py
# ============================================================

import os
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("intel.tuner")

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DEALS_FILE = CRYPTO_ROOT / "data" / "signals" / "closed_deals.json"
CONFIG_FILE = CRYPTO_ROOT / "data" / "intel" / "bot_config.json"
BOT_INFO_FILE = CRYPTO_ROOT / "data" / "signals" / "bot_info.json"
TRAINER_SCRIPT = CRYPTO_ROOT / "learn" / "weekly_trainer.py"

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
    except Exception as e:
        log.error(f"Ошибка записи {path}: {e}")
        if temp_path.exists(): temp_path.unlink()

class SimpleADWIN:
    """Упрощенный детектор дрифта ADWIN для потоковых данных."""
    def __init__(self, delta=0.002):
        self.delta = delta
        self.window = []
        self.variance = 0.0
        self.n = 0

    def update(self, value):
        self.window.append(value)
        self.n += 1
        if self.n < 10:
            self.variance = (self.variance * (self.n - 1) + (value - (sum(self.window) / self.n)) ** 2) / self.n if self.n > 1 else 0
            return False
        
        mean = sum(self.window) / self.n
        self.variance = (self.variance * (self.n - 1) + (value - mean) ** 2) / self.n
        
        # Упрощенная проверка на разладку (сравнение первой и второй половины окна)
        if self.n >= 20:
            mid = self.n // 2
            mean1 = sum(self.window[:mid]) / mid
            mean2 = sum(self.window[mid:]) / (self.n - mid)
            epsilon = (1.0 / (2 * self.n)) * (self.variance ** 0.5) * ((4 / self.delta) ** 0.5)
            if abs(mean1 - mean2) > epsilon and abs(mean1 - mean2) > 0.1:
                return True
        return False

def check_retrain_triggers(mode, side, recent_deals, config, bot_info):
    rt_cfg = config.get("retrain_triggers", {})
    if not rt_cfg.get("enabled", False): return False

    min_trades = int(rt_cfg.get("min_trades_eval", 10))
    if len(recent_deals) < min_trades: return False

    # 1. Триггер по винрейту
    wins = sum(1 for d in recent_deals if d.get("pnl_usd", 0) > 0)
    winrate = wins / len(recent_deals) if recent_deals else 0
    winrate_threshold = float(rt_cfg.get(f"winrate_drop_{mode}", 0.40 if mode == "intraday" else 0.35))
    
    trigger_fired = False
    reason = ""

    if winrate < winrate_threshold:
        trigger_fired = True
        reason = f"Winrate {winrate:.2%} < {winrate_threshold:.2%}"

    # 2. Триггер ADWIN по серии ошибок (1 = убыток, 0 = прибыль)
    if not trigger_fired:
        adwin = SimpleADWIN(delta=float(rt_cfg.get("adwin_delta", 0.002)))
        errors = [1 if d.get("pnl_usd", 0) < 0 else 0 for d in reversed(recent_deals)]
        for err in errors:
            if adwin.update(err):
                trigger_fired = True
                reason = "ADWIN Concept Drift Detected"
                break

    if not trigger_fired: return False

    # Защита от спама (Cooldown)
    key = f"last_retrain_{mode}_{side}"
    last_retrain_str = bot_info.get(key)
    now_utc = datetime.now(timezone.utc)
    cooldown_h = float(rt_cfg.get(f"cooldown_hours_{mode}", 12 if mode == "intraday" else 48))

    if last_retrain_str:
        last_retrain = datetime.fromisoformat(last_retrain_str)
        if (now_utc - last_retrain).total_seconds() / 3600.0 < cooldown_h:
            log.info(f"[{mode.upper()}/{side.upper()}] Триггер переобучения ({reason}), но кулдаун еще не прошел.")
            return False

    log.warning(f"🚀 [{mode.upper()}/{side.upper()}] ДОСРОЧНОЕ ПЕРЕОБУЧЕНИЕ: {reason}")
    bot_info[key] = now_utc.isoformat()
    return True

def tune_hedge_weights(mode, side, recent_deals, config, bot_info):
    """Онлайн-обновление весов индикаторов по алгоритму Hedge/EWA."""
    hedge_cfg = config.get("hedge_learning", {})
    if not hedge_cfg.get("enabled", False): return

    lr = float(hedge_cfg.get("learning_rate", 0.5))
    min_w = float(hedge_cfg.get("min_weight", 0.1))
    max_w = float(hedge_cfg.get("max_weight", 2.0))
    cfg = config[mode]
    weights = cfg.get("weights", {})

    for deal in recent_deals:
        pnl = deal.get("pnl_usd", 0)
        if pnl == 0: continue
        
        # Loss: если минус, то 1.0, если плюс, то 0.0. 
        # Можно сгладить: loss = max(0, -pnl / initial_balance)
        loss = 1.0 if pnl < 0 else 0.0
        multiplier = 2.71828 ** (-lr * loss)

        for sig in deal.get("active_signals", []):
            if sig in weights:
                old_w = weights[sig]
                if pnl > 0:
                    new_w = old_w * (2 - multiplier) # При профите немного растим
                else:
                    new_w = old_w * multiplier      # При убытке режем экспоненциально
                weights[sig] = round(max(min_w, min(max_w, new_w)), 3)

    # Нормализация (чтобы сумма весов не улетала в космос)
    total_w = sum(weights.values())
    if total_w > 0:
        for k in weights:
            weights[k] = round(weights[k] / total_w * len(weights), 3)

    cfg["weights"] = weights

def tune_thresholds(mode, side, deals_history, config, bot_info):
    """Антипаралич: снижение порога при долгом простое."""
    cfg = config[mode]
    idle_timeout = float(cfg.get("idle_timeout_h", 3.0 if mode == "intraday" else 24.0))
    thresh_key = f"threshold_{side}"
    current_thresh = float(cfg.get(thresh_key, 5.0))
    min_thresh = float(config.get("adaptive_threshold", {}).get("min_threshold", 1.5))

    now_utc = datetime.now(timezone.utc)
    last_trade_time = None
    for d in deals_history:
        if d.get("mode") == mode and d.get("type", "").upper() == side.upper():
            last_trade_time = datetime.fromisoformat(d["close_time"])
            break

    if last_trade_time:
        idle_hours = (now_utc - last_trade_time).total_seconds() / 3600.0
    else:
        # Если сделок вообще не было, используем время старта бота
        started_at = bot_info.get(mode, {}).get("started_at")
        if started_at:
            idle_hours = (now_utc - datetime.fromisoformat(started_at)).total_seconds() / 3600.0
        else:
            idle_hours = idle_timeout + 1 # Принудительно снижаем

    if idle_hours >= idle_timeout and current_thresh > min_thresh:
        # Защита от "дрожания": меняем не чаще, чем раз в 2 часа
        key = f"last_threshold_change_{mode}_{side}"
        last_change_str = bot_info.get(key)
        if last_change_str:
            last_change = datetime.fromisoformat(last_change_str)
            if (now_utc - last_change).total_seconds() < 7200:
                return

        step = 0.05 if mode == "swing" else 0.1
        new_thresh = max(min_thresh, current_thresh - step)
        cfg[thresh_key] = round(new_thresh, 2)
        bot_info[key] = now_utc.isoformat()
        log.info(f"📉 [{mode.upper()}/{side.upper()}] Простой {idle_hours:.1f}ч. Снижаем порог {current_thresh} -> {new_thresh}")

def analyze_deals_and_tune():
    log.info("🧠 Self-Adaptive Tuner v3.0 запущен")
    deals = load_json(DEALS_FILE, [])
    config = load_json(CONFIG_FILE, None)
    bot_info = load_json(BOT_INFO_FILE, {})
    
    if not config: return log.error("bot_config.json не найден")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24 * 7) # Берем окно на неделю для оценки

    need_retrain = False

    for mode in ["intraday", "swing"]:
        for side in ["long", "short"]:
            # Отбираем историю строго для этого сектора
            sector_deals = [
                d for d in deals 
                if d.get("mode") == mode and d.get("type", "").upper() == side.upper() 
                and "close_time" in d
            ]
            
            if not sector_deals: continue

            # 1. Hedge веса
            tune_hedge_weights(mode, side, sector_deals[-10:], config, bot_info)
            
            # 2. Пороги
            tune_thresholds(mode, side, sector_deals, config, bot_info)

            # 3. Переобучение
            if check_retrain_triggers(mode, side, sector_deals[-20:], config, bot_info):
                need_retrain = True

    config["updated_at"] = now.isoformat()
    save_json_atomic(CONFIG_FILE, config)
    save_json_atomic(BOT_INFO_FILE, bot_info)

    if need_retrain:
        log.info("🔄 Запуск фонового переобучения weekly_trainer.py...")
        try:
            subprocess.run([sys.executable, str(TRAINER_SCRIPT)], check=False)
        except Exception as e:
            log.error(f"Не удалось запустить тренер: {e}")

if __name__ == "__main__":
    analyze_deals_and_tune()