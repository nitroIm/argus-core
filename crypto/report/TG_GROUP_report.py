# ============================================================
# ARGUS - TELEGRAM GROUP REPORT
# Path: crypto/report/TG_GROUP_report.py
# ============================================================

import os
import json
import logging
import requests
import io
from pathlib import Path
from datetime import datetime, timezone
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.report")

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
SIGNALS_DIR = CRYPTO_ROOT / "data" / "signals"

BOT_INFO = SIGNALS_DIR / "bot_info.json"
CLOSED_DEALS = SIGNALS_DIR / "closed_deals.json"

# --- НАСТРОЙКИ ---
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_GROUP_ID") or "-1003946449078").strip() # Твоя бесплатная группа
PAID_GROUP_LINK = "https://t.me/+rxoHVrXih2k3YzUy" # Твоя закрытая ссылка

def load_json(path, default=None):
    if not path.exists(): return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception as e:
        log.error(f"Ошибка чтения {path}: {e}")
        return default if default is not None else {}

def send_tg_photo(image_bytes, caption):
    if not BOT_TOKEN or not CHAT_ID:
        log.error("Не заданы TELEGRAM_BOT_TOKEN или TELEGRAM_GROUP_ID")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {'photo': ('chart.png', image_bytes, 'image/png')}
    data = {'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}
    
    r = requests.post(url, data=data, files=files, timeout=30)
    if r.status_code == 200:
        return True
    else:
        log.error(f"Ошибка отправки в ТГ: {r.status_code} - {r.text}")
        return False

def generate_chart(deals, bot_info):
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor('#1e1e1e')
    ax.set_facecolor('#1e1e1e')
    
    ax.tick_params(colors='white')
    ax.xaxis.label.set_color('white')
    ax.yaxis.label.set_color('white')
    ax.title.set_color('white')
    ax.grid(color='gray', linestyle='--', linewidth=0.5, alpha=0.5)
    for spine in ax.spines.values(): spine.set_edgecolor('gray')

    intraday_dates, intraday_balances = [], []
    swing_dates, swing_balances = [], []
    
    start_date = None
    for mode in ["intraday", "swing"]:
        s_at = bot_info.get(mode, {}).get("started_at")
        if s_at:
            dt = datetime.fromisoformat(s_at)
            if start_date is None or dt < start_date: start_date = dt

    if start_date:
        intraday_dates.append(start_date)
        intraday_balances.append(bot_info.get("intraday", {}).get("start_balance", 100.0))
        swing_dates.append(start_date)
        swing_balances.append(bot_info.get("swing", {}).get("start_balance", 100.0))

    valid_deals = [d for d in deals if "close_time" in d and "mode" in d]
    valid_deals.sort(key=lambda x: x["close_time"])

    for d in valid_deals:
        try:
            dt = datetime.fromisoformat(d["close_time"])
            mode = d["mode"]
            if "balance_after" not in d: continue 
            
            if mode == "intraday":
                intraday_dates.append(dt)
                intraday_balances.append(d["balance_after"])
            elif mode == "swing":
                swing_dates.append(dt)
                swing_balances.append(d["balance_after"])
        except Exception:
            continue

    if intraday_dates: ax.plot(intraday_dates, intraday_balances, color='#00d2ff', linewidth=2, marker='o', markersize=4, label='INTRADAY')
    if swing_dates: ax.plot(swing_dates, swing_balances, color='#00ff87', linewidth=2, marker='o', markersize=4, label='SWING')

    ax.set_title("Динамика баланса", fontsize=14, pad=15)
    ax.set_ylabel("Баланс ($)")
    ax.legend(facecolor='#1e1e1e', edgecolor='gray', labelcolor='white')
    
    if intraday_dates or swing_dates:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m %H:%M'))
        fig.autofmt_xdate()

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)
    return buf.getvalue()

def main():
    log.info("STARTING REPORT GENERATION")
    
    bot_info = load_json(BOT_INFO, {})
    deals = load_json(CLOSED_DEALS, [])
    
    if not bot_info or not isinstance(deals, list):
        return log.error("Нет данных для отчета.")

    now = datetime.now(timezone.utc)
    today_str = now.strftime("%d.%m.%Y")
    
    # Фильтр сделок за сегодня
    today_deals = []
    for d in deals:
        if "close_time" in d:
            try:
                close_date = datetime.fromisoformat(d["close_time"]).date()
                if close_date == now.date():
                    today_deals.append(d)
            except: pass

    # --- Подсчет статистики за СЕГОДНЯ ---
    today_total = len(today_deals)
    today_wins = sum(1 for d in today_deals if d.get("pnl_usd", 0) > 0)
    today_losses = today_total - today_wins
    today_pnl = sum(d.get("pnl_usd", 0) for d in today_deals)
    
    # --- Подсчет ОБЩЕЙ статистики ---
    total_deals = len(deals)
    total_wins = sum(1 for d in deals if d.get("pnl_usd", 0) > 0)
    overall_winrate = (total_wins / total_deals * 100) if total_deals > 0 else 0.0
    
    bal_intra = bot_info.get("intraday", {}).get("balance", 100.0)
    bal_swing = bot_info.get("swing", {}).get("balance", 100.0)
    start_intra = bot_info.get("intraday", {}).get("start_balance", 100.0)
    start_swing = bot_info.get("swing", {}).get("start_balance", 100.0)
    
    total_bal = bal_intra + bal_swing
    total_pnl = (bal_intra - start_intra) + (bal_swing - start_swing)
    
    start_dt = None
    for mode in ["intraday", "swing"]:
        s_at = bot_info.get(mode, {}).get("started_at")
        if s_at:
            dt = datetime.fromisoformat(s_at)
            if start_dt is None or dt < start_dt: start_dt = dt
    start_str = start_dt.strftime("%d.%m.%Y") if start_dt else "?"

    # Формируем красивый текст
    pnl_sign_today = "+" if today_pnl >= 0 else ""
    pnl_sign_total = "+" if total_pnl >= 0 else ""
    
    caption = (
        f"🤖 <b>Отчёт торгового бота за {today_str}</b>\n\n"
        f"📊 <b>РЕЗУЛЬТАТ ЗА СЕГОДНЯ:</b>\n"
        f"Сделок: {today_total} ({today_wins} ✅ / {today_losses} ❌)\n"
        f"PnL за день: <b>{pnl_sign_today}${today_pnl:.2f}</b>\n\n"
        
        f"⚡ <b>INTRADAY (всего):</b>\n"
        f"Сделок: {len([d for d in deals if d.get('mode') == 'intraday'])} | Баланс: <b>${bal_intra:.2f}</b>\n\n"
        
        f"🌊 <b>SWING (всего):</b>\n"
        f"Сделок: {len([d for d in deals if d.get('mode') == 'swing'])} | Баланс: <b>${bal_swing:.2f}</b>\n\n"
        
        f"💼 <b>ОБЩИЙ ИТОГ (с {start_str}):</b>\n"
        f"Всего сделок: {total_deals} | Винрейт: {overall_winrate:.1f}%\n"
        f"Общий баланс: <b>${total_bal:.2f}</b>\n"
        f"Общий PnL: <b>{pnl_sign_total}${total_pnl:.2f}</b>\n\n"
        
        f"⚠️ <i>Не является инвестиционной рекомендацией.</i>\n"
        f"🔒 <b>Реальные входы в моменте — в закрытом канале:</b>\n{PAID_GROUP_LINK}"
    )

    log.info("Генерация графика...")
    chart_bytes = generate_chart(deals, bot_info)
    
    log.info("Отправка в Telegram...")
    if send_tg_photo(chart_bytes, caption):
        log.info("✅ Отчет успешно отправлен!")
    else:
        log.error("❌ Ошибка при отправке отчета.")

if __name__ == "__main__":
    main()