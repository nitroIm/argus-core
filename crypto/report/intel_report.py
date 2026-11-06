# ============================================================
# ARGUS - INTEL REPORT (v1.0)
# Path: crypto/report/intel_report.py
# ============================================================

import os
import sys
import json
import logging
import requests
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("intel_report")

SCRIPT_DIR = Path(__file__).resolve().parent
# Путь к данным: crypto/report/ -> crypto/data/intel/
DATA_FILE = SCRIPT_DIR.parent / "data" / "intel" / "correlations.json"

BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
TZ = ZoneInfo("Europe/Kaliningrad")

def load_json(path):
    if not path.exists():
        log.error(f"Файл не найден: {path}")
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Ошибка чтения JSON: {e}")
        return None

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        log.error("Нет токена или chat_id для Telegram")
        return False
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
        if r.status_code == 200:
            return True
        log.error(f"TG API Error: {r.text}")
    except Exception as e:
        log.error(f"TG Exception: {e}")
    return False

def main():
    # Получаем fail_count от runner.py (если передали)
    fail_count = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    
    log.info("Формирование отчета Intel...")
    
    data = load_json(DATA_FILE)
    if not data or "history" not in data or not data["history"]:
        log.warning("Нет данных в correlations.json")
        return

    # Берем самый свежий день (последний в списке)
    latest = data["history"][-1]
    corrs = latest.get("correlations", {})
    
    if not corrs:
        log.warning("Словарь корреляций пуст")
        return

    # Сортируем по силе связи (по модулю), чтобы минусовые тоже попадали в топ
    sorted_corrs = sorted(corrs.items(), key=lambda x: abs(x[1].get("best_corr", 0)), reverse=True)
    top_5 = sorted_corrs[:5]

    now = datetime.now(timezone.utc).astimezone(TZ)
    
    lines = [
        f"📊 <b>ARGUS Intel — {now.strftime('%d.%m.%Y, %H:%M')}</b>",
        ""
    ]

    if fail_count > 0:
        lines.append(f"⚠️ <b>Внимание:</b> {fail_count} модуль(ей) упали. Данные могут быть неполными.")
        lines.append("")

    lines.append("🔗 <b>Топ-5 связей с BTC (90 дней):</b>")
    
    for ticker, info in top_5:
        corr = info.get("best_corr", 0)
        lag = info.get("best_lag_h", 0)
        lag_str = f"{lag:+d}ч" if lag != 0 else "0ч"
        emoji = "🔹" if corr > 0 else "🔸"
        
        lines.append(f"{emoji} {ticker}: {corr:+.2f} (лаг {lag_str})")

    lines.append("")
    lines.append("<i>Анализ связей завершен.</i>")

    text = "\n".join(lines)
    if send_telegram(text):
        log.info("Отчет отправлен в личку.")
    else:
        log.error("Не удалось отправить отчет.")

if __name__ == "__main__":
    main()