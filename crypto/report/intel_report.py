# ============================================================
# ARGUS - INTEL REPORT (v1.2 - Beta & Price)
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

    latest = data["history"][-1]
    corrs = latest.get("correlations", {}).copy()
    
    # Извлекаем текущую цену BTC
    btc_price = corrs.pop("current_btc_price", 0)
    if not btc_price:
        btc_price = 0 # Защита, если цена не посчиталась
        
    if not corrs:
        log.warning("Словарь корреляций пуст")
        return

    # Разделяем на 3 группы
    leading_btc = []  # Актив ведет BTC (лаг > 0)
    lagging_btc = []  # BTC ведет актив (лаг < 0)
    sync_btc = []     # Синхронно (лаг 0)

    for ticker, info in corrs.items():
        lag = info.get("best_lag_h", 0)
        if lag > 0: leading_btc.append((ticker, info))
        elif lag < 0: lagging_btc.append((ticker, info))
        else: sync_btc.append((ticker, info))

    # Сортируем каждую группу по силе связи (по модулю)
    for lst in [leading_btc, lagging_btc, sync_btc]:
        lst.sort(key=lambda x: abs(x[1].get("best_corr", 0)), reverse=True)

    now = datetime.now(timezone.utc).astimezone(TZ)
    
    lines = [
        f"📊 <b>ARGUS Intel — {now.strftime('%d.%m.%Y, %H:%M')}</b> | BTC: ${btc_price:,.0f}",
        ""
    ]

    if fail_count > 0:
        lines.append(f"⚠️ <b>Внимание:</b> {fail_count} модуль(ей) упали. Данные могут быть неполными.")
        lines.append("")

    def format_group(title, group_list):
        if not group_list: return []
        res = [title]
        # Ограничиваем вывод топ-10, чтобы сообщение влезло в лимит Телеграма (4096 символов)
        for ticker, info in group_list[:10]:
            corr = info.get("best_corr", 0)
            lag = info.get("best_lag_h", 0)
            beta = info.get("beta", 0)
            
            # Рассчитываем примерное изменение цены BTC при движении актива на 1%
            est_pct = beta * 1.0
            est_price = btc_price * (1 + (est_pct / 100))
            
            emoji = "🔹" if corr > 0 else "🔸"
            lag_str = f"{lag:+d}ч" if lag != 0 else "0ч"
            
            # Форматируем блок
            res.append(f"{emoji} <b>{ticker}</b>: corr {corr:+.2f} (лаг {lag_str})")
            if beta != 0 and btc_price > 0:
                res.append(f"   └ Beta {beta:+.2f}: при +1% актива, BTC {est_pct:+.2f}% (~${est_price:,.0f})")
        res.append("")
        return res

    # Собираем единое сообщение
    lines.extend(format_group("📈 <b>Активы, которые ведут BTC (крипто-бот):</b>", leading_btc))
    lines.extend(format_group("📉 <b>Активы, которые ведет BTC (фондовый бот):</b>", lagging_btc))
    lines.extend(format_group("⏱ <b>Синхронные (лаг 0ч):</b>", sync_btc))

    lines.append("<i>* Показан ТОП-10 в каждой категории. Beta — это % изменения BTC при движении актива на +1%.</i>")

    text = "\n".join(lines)
    if send_telegram(text):
        log.info("Отчет отправлен в личку.")
    else:
        log.error("Не удалось отправить отчет.")

if __name__ == "__main__":
    main()