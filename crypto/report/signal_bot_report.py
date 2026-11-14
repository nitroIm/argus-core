# ============================================================
# ARGUS - SIGNAL REPORT v6.0 (CHARTS & MARKERS)
# Path: crypto/report/signal_bot_report.py
# ============================================================

import os, json, logging, requests, io
from pathlib import Path
from datetime import datetime, timezone, timedelta
import psycopg
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.signal_report")

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
SIGNALS_DIR = CRYPTO_ROOT / "data" / "signals"

INFO_DEAL = SIGNALS_DIR / "info_deal.json"
CLOSED_DEALS = SIGNALS_DIR / "closed_deals.json"
BOT_INFO = SIGNALS_DIR / "bot_info.json"

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
# ДЛЯ ТЕСТА ОТПРАВЛЯЕМ В ЛИЧКУ (TELEGRAM_CHAT_ID), А НЕ В КАНАЛ
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

def load_json(path, default=None):
    if not path.exists(): return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default if default is not None else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def send_tg_photo(image_bytes, caption):
    if not BOT_TOKEN or not CHAT_ID: return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {'photo': ('chart.png', image_bytes, 'image/png')}
    data = {'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}
    r = requests.post(url, data=data, files=files, timeout=20)
    return r.status_code == 200

def send_tg_text(text):
    if not BOT_TOKEN or not CHAT_ID: return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=20)
    return r.status_code == 200

def fmt(p): return "$" + format(int(p), ",") if p >= 1000 else "$" + format(p, ".2f")

def get_db_candles(start_time_str, end_time_str=None):
    """Достаем свечи из базы для графика"""
    if not DB_URL: return None
    try:
        conn = psycopg.connect(DB_URL, connect_timeout=10)
        query = "SELECT open_time, open, high, low, close, volume FROM candles WHERE symbol='BTCUSDT' AND timeframe='15m' AND market_type='spot' AND open_time >= %s"
        params = [start_time_str]
        
        if end_time_str:
            query += " AND open_time <= %s"
            params.append(end_time_str)
            
        query += " ORDER BY open_time ASC"
        
        with conn.cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
        conn.close()
        
        if not rows: return None
        
        df = pd.DataFrame(rows, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['Date'] = pd.to_datetime(df['Date'], utc=True)
        df.set_index('Date', inplace=True)
        return df
    except Exception as e:
        log.error(f"DB Error: {e}")
        return None

def draw_chart(event):
    """Рисует свечной график с отметками"""
    mode = event.get("mode", "swing")
    deal_type = event.get("deal_type", "LONG")
    
    if event["type"] == "ENTRY":
        # Берем 40 свечей назад от текущего момента
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=10)
        df = get_db_candles(start_time.isoformat(), end_time.isoformat())
        title = f"Вход {deal_type}"
    else: # CLOSE
        open_time = event.get("open_time")
        if not open_time: return None
        # Берем чуть больше времени, чтобы показать контекст до входа и после выхода
        start_dt = datetime.fromisoformat(open_time) - timedelta(hours=2)
        end_dt = datetime.now(timezone.utc) + timedelta(minutes=30)
        df = get_db_candles(start_dt.isoformat(), end_dt.isoformat())
        title = f"Выход {deal_type}"

    if df is None or df.empty:
        return None

    # Настройки стиля (темная тема)
    mc = mpf.make_marketcolors(up='#00ff87', down='#ff0044', edge='inherit', wick='inherit', volume='in')
    s  = mpf.make_mpf_style(marketcolors=mc, gridstyle='--', y_on_right=True, facecolor='#1e1e1e', edgecolor='gray', figcolor='#1e1e1e', gridcolor='#333333', rc={'axes.labelcolor': 'white', 'xtick.color': 'white', 'ytick.color': 'white'})

    # Линии уровней
    hlines = dict(hlines=[], colors=[], linewidths=[])
    entry_price = float(event.get("entry", 0))
    
    if event["type"] == "ENTRY":
        sl_price = float(event["sl"])
        tp_price = float(event["tp"])
        hlines['hlines'].extend([entry_price, sl_price, tp_price])
        hlines['colors'].extend(['blue', 'red', 'green'])
        hlines['linewidths'].extend([1.5, 1.5, 1.5])
    else:
        exit_price = float(event.get("exit", 0))
        hlines['hlines'].extend([entry_price, exit_price])
        hlines['colors'].extend(['blue', 'orange'])
        hlines['linewidths'].extend([1.5, 1.5])

    # Пометки Buy/Sell (стрелочки)
    apds = []
    if event["type"] == "ENTRY":
        marker_price = entry_price
        marker_type = '^' if deal_type == "LONG" else 'v'
        marker_color = '#00ff87' if deal_type == "LONG" else '#ff0044'
        scatter = pd.Series([marker_price], index=[df.index[-1]])
        apds.append(mpf.make_addplot(scatter, type='scatter', markersize=200, marker=marker_type, color=marker_color))
    else:
        entry_dt = datetime.fromisoformat(event["open_time"])
        # Ищем ближайшую свечу к времени входа
        closest_entry_idx = df.index.get_indexer([entry_dt], method='nearest')[0]
        scatter_entry = pd.Series([entry_price], index=[df.index[closest_entry_idx]])
        
        exit_dt = datetime.now(timezone.utc)
        scatter_exit = pd.Series([float(event["exit"])], index=[df.index[-1]])
        
        marker_entry = '^' if deal_type == "LONG" else 'v'
        marker_exit = 'v' if deal_type == "LONG" else '^'
        
        apds.append(mpf.make_addplot(scatter_entry, type='scatter', markersize=200, marker=marker_entry, color='#00ff87'))
        apds.append(mpf.make_addplot(scatter_exit, type='scatter', markersize=200, marker=marker_exit, color='#ff0044'))

    # Отрисовка
    buf = io.BytesIO()
    fig, axes = mpf.plot(
        df, type='candle', style=s, title=title, 
        addplot=apds, hlines=hlines, 
        figsize=(10, 6), returnfig=True, 
        volume=False, warn_too_much_data=1000
    )
    fig.savefig(buf, format='png', dpi=120, facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()

def get_stats_text(mode):
    # (Оставляем как было)
    info = load_json(BOT_INFO, {}).get(mode, {})
    closed = [t for t in load_json(CLOSED_DEALS, []) if t.get("mode") == mode]
    total = len(closed)
    wins = sum(1 for t in closed if t.get("pnl_usd", 0) > 0)
    losses = total - wins
    winrate = (wins / total * 100) if total > 0 else 0
    balance = info.get("balance", 100.0)
    total_pnl = balance - info.get("start_balance", 100.0)
    pnl_sign = "+" if total_pnl >= 0 else ""
    return (f"────────────────────\n"
            f"📊 <b>Статистика {mode.upper()}:</b>\n"
            f"📈 Сделок: {total} ({wins} ✅ / {losses} ❌)\n"
            f"🎯 Винрейт: <b>{winrate:.1f}%</b>\n"
            f"💰 Баланс: <b>${balance:.2f}</b> (PnL: <b>{pnl_sign}${total_pnl:.2f}</b>)")

def main():
    log.info("STARTING SIGNAL REPORT v6.0 (WITH CHARTS)")
    queue = load_json(INFO_DEAL, [])
    if not isinstance(queue, list) or not queue:
        return log.info("No new events in queue.")
        
    sent_any = False
    for event in queue:
        mode_name = "⚡ ИНТРАДЕЙ" if event.get("mode") == "intraday" else "🌊 СВИНГ"
        deal_type = event.get("deal_type", "LONG")
        text = ""
        
        # 1. ОТКРЫТИЕ СДЕЛКИ
        if event["type"] == "ENTRY":
            entry, tp, sl = float(event["entry"]), float(event["tp"]), float(event["sl"])
            action = "ПОКУПКА" if deal_type == "LONG" else "ПРОДАЖА"
            text = f"🟢 <b>ОТКРЫТА СДЕЛКА: {mode_name}</b>\n\n<b>{action} BTC ({deal_type})</b>\n🟢 <b>Вход:</b> {fmt(entry)}\n"
            if deal_type == "LONG":
                text += f"🎯 <b>Тейк:</b> {fmt(tp)} ({(tp-entry)/entry*100:+.2f}%)\n🛑 <b>Стоп:</b> {fmt(sl)} ({(sl-entry)/entry*100:+.2f}%)\n"
            else:
                text += f"🎯 <b>Тейк:</b> {fmt(tp)} ({(entry-tp)/entry*100:+.2f}%)\n🛑 <b>Стоп:</b> {fmt(sl)} ({(entry-sl)/entry*100:+.2f}%)\n"
            
            text += "\n" + get_stats_text(event.get("mode", "swing"))
            
            chart = draw_chart(event)
            if chart:
                if send_tg_photo(chart, text): sent_any = True
            else:
                if send_tg_text(text): sent_any = True

        # 2. ПОДТЯЖКА СТОПА (ТРЕЙЛИНГ)
        elif event["type"] == "UPDATE":
            text = (f"🟡 <b>ПОДТЯЖКА СТОПА: {mode_name}</b>\n\n"
                    f"<b>Сделка {deal_type} все еще в работе!</b>\n"
                    f"🛑 <b>Новый стоп:</b> {fmt(event['new_sl'])} ({event['sl_pct']:+.2f}%)\n"
                    f"<i>Бот защищает профит.</i>")
            if send_tg_text(text): sent_any = True

        # 3. ЗАКРЫТИЕ СДЕЛКИ
        elif event["type"] == "CLOSE":
            pnl = event["pnl_usd"]
            sign = "+" if pnl >= 0 else ""
            status = "✅ УСПЕХ" if pnl > 0 else "❌ УБЫТОК"
            reason_raw = event.get("reason", "UNKNOWN")
            reasons_ru = {
                "STOP_LOSS": "Сработал стоп-лосс",
                "TAKE_PROFIT": "Достигнут тейк-профит",
                "TRAILING_STOP": "Сработал трейлинг-стоп",
                "TIME_EXIT_DAY": "Закрытие по времени (конец дня)",
                "TIME_EXIT": "Закрытие по времени"
            }
            reason_text = reasons_ru.get(reason_raw, reason_raw)
            
            text = (f"🔴 <b>СДЕЛКА ЗАКРЫТА: {mode_name}</b>\n\n"
                    f"<b>Результат ({deal_type}): {status}</b>\n"
                    f"📊 <b>Причина:</b> {reason_text}\n"
                    f"💰 <b>PnL:</b> {sign}{event['pnl_pct']}% ({sign}${pnl:.2f})\n"
                    f"💼 <b>Баланс:</b> ${event.get('new_balance', 0):.2f}\n")
            
            text += "\n" + get_stats_text(event.get("mode", "swing"))

            chart = draw_chart(event)
            if chart:
                if send_tg_photo(chart, text): sent_any = True
            else:
                if send_tg_text(text): sent_any = True

        if sent_any:
            log.info(f"Sent {event['type']} ({event.get('mode')}) to TG.")
        
    if sent_any:
        save_json(INFO_DEAL, [])
        log.info("Queue cleared.")

if __name__ == "__main__":
    main()