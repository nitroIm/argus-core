import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("intel.tuner")

# --- ПУТИ (строго по зафиксированной структуре) ---
SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent # Папка crypto

DEALS_FILE = CRYPTO_ROOT / "data" / "signals" / "closed_deals.json"
CONFIG_FILE = CRYPTO_ROOT / "data" / "intel" / "bot_config.json"

# --- НАСТРОЙКИ ТЮНЕРА ---
LOOKBACK_HOURS = 24        # Анализируем сделки за последние 24 часа
MIN_TRADES_TO_TUNE = 2     # Нужно хотя бы 2 сделки для корректировки
THRESHOLD_STEP = 0.1       # На сколько шагов двигаем порог за раз
MIN_THRESHOLD = 1.5        # Нижняя граница порога
MAX_THRESHOLD = 5.0        # Верхняя граница порога

def load_json(path, default=None):
    if not path.exists(): return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception as e:
        log.error(f"Ошибка чтения {path}: {e}")
        return default if default is not None else {}

def save_json_atomic(path, data):
    """Безопасная запись: пишем во временный файл, затем переименовываем. 
       Бот никогда не прочитает недописанный файл."""
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
        log.error("Конфиг bot_config.json не найден или пуст. Прерываем работу.")
        return

    now = datetime.now(timezone.utc)
    cutoff_time = now - timedelta(hours=LOOKBACK_HOURS)

    # Разделяем закрытые сделки по режимам за последние 24 часа
    recent_intraday = []
    recent_swing = []
    
    for d in deals:
        if "close_time" not in d or "mode" not in d: continue
        
        try:
            close_time = datetime.fromisoformat(d["close_time"])
            if close_time.tzinfo is None:
                close_time = close_time.replace(tzinfo=timezone.utc)
                
            if close_time >= cutoff_time:
                if d["mode"] == "intraday": recent_intraday.append(d)
                elif d["mode"] == "swing": recent_swing.append(d)
        except ValueError:
            continue

    # --- ЛОГИКА НАСТРОЙКИ ДЛЯ INTRADAY ---
    _tune_mode("intraday", recent_intraday, config)
    
    # --- ЛОГИКА НАСТРОЙКИ ДЛЯ SWING ---
    _tune_mode("swing", recent_swing, config)

    # Обновляем время последнего запуска и сохраняем
    config["updated_at"] = now.isoformat()
    save_json_atomic(CONFIG_FILE, config)

def _tune_mode(mode_name, deals, config):
    """Внутренняя функция для анализа конкретного режима"""
    if len(deals) < MIN_TRADES_TO_TUNE:
        log.info(f"[{mode_name.upper()}] Мало сделок за 24ч ({len(deals)}). Порог не меняем.")
        return

    wins = sum(1 for d in deals if d.get("pnl_usd", 0) > 0)
    winrate = (wins / len(deals)) * 100
    current_thresh = config[mode_name].get("threshold", 2.5)
    new_thresh = current_thresh

    log.info(f"[{mode_name.upper()}] Сделок: {len(deals)} | Winrate: {winrate:.1f}% | Текущий порог: {current_thresh}")

    # Если винрейт плохой - поднимаем порог (берем только сильные сигналы)
    if winrate < 40.0:
        new_thresh = min(MAX_THRESHOLD, current_thresh + THRESHOLD_STEP)
        log.info(f"🔻 Винрейт < 40%. Поднимаем порог с {current_thresh} до {new_thresh}")
        
    # Если винрейт отличный - можем снизить порог (берем больше сигналов)
    elif winrate > 65.0:
        new_thresh = max(MIN_THRESHOLD, current_thresh - THRESHOLD_STEP)
        log.info(f"🔺 Винрейт > 65%. Снижаем порог с {current_thresh} до {new_thresh}")
    else:
        log.info(f"⚖️ Винрейт в норме (40-65%). Порог оставляем {current_thresh}")

    config[mode_name]["threshold"] = round(new_thresh, 2)

if __name__ == "__main__":
    analyze_deals_and_tune()