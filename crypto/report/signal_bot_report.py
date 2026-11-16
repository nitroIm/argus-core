# ============================================================
# ARGUS - SIGNAL REPORT v8.8.0
# Path: crypto/report/signal_bot_report.py
#
# ИЗМЕНЕНИЯ v8.8.0 (относительно v8.7.2):
#   - Сообщения и графики теперь отправляются В ЛИЧКУ пользователю
#     (секрет TELEGRAM_CHAT_ID), а не только в канал.
#   - Канал сохранён как опция: если в workflow задать
#     SEND_TO_CHANNEL=true, сообщения дублируются в TG_CHANNEL_ID.
#   - Поддержка v9.5.1/9.5.2 (tp1/tp2) сохранена.
# ============================================================

import os, json, logging, requests, io
from pathlib import Path
from datetime import datetime, timezone, timedelta
import psycopg
import pandas as pd
import numpy as np
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
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()          # ЛИЧКА — основной получатель
CHANNEL_ID = (os.getenv("TG_CHANNEL_ID") or "").strip()           # канал — опционально
SEND_TO_CHANNEL = (os.getenv("SEND_TO_CHANNEL") or "").strip().lower() in ("1", "true", "yes")

def load_json(path, default=None):
    if not path.exists(): return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default if default is not None else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def send_tg_photo(image_bytes, caption, chat_id):
    if not BOT_TOKEN or not chat_id: return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {'photo': ('chart.png', image_bytes, 'image/png')}
    data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}
    r = requests.post(url, data=data, files=files, timeout=30)
    if r.status_code != 200: log.error(f"TG Photo Error [{chat_id}]: {r.text}")
    return r.status_code == 200

def send_tg_text(text, chat_id):
    if not BOT_TOKEN or not chat_id: return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=20)
    if r.status_code != 200: log.error(f"TG Text Error [{chat_id}]: {r.text}")
    return r.status_code == 200

def send_to_all(image_bytes, caption):
    """Личка — основной получатель. Канал — только если включён SEND_TO_CHANNEL."""
    sent = False
    if CHAT_ID:
        if image_bytes:
            sent = send_tg_photo(image_bytes, caption, CHAT_ID)
        else:
            sent = send_tg_text(caption, CHAT_ID)
    if SEND_TO_CHANNEL and CHANNEL_ID:
        try:
            if image_bytes:
                send_tg_photo(image_bytes, caption, CHANNEL_ID)
            else:
                send_tg_text(caption, CHANNEL_ID)
        except Exception as e:
            log.error(f"Channel send failed: {e}")
    return sent

def fmt(p): return "$" + format(int(p), ",") if p >= 1000 else "$" + format(p, ".2f")

def get_db_candles():
    if not DB_URL: return None
    try:
        conn = psycopg.connect(DB_URL, connect_timeout=10)
        query = "SELECT open_time, open, high, low, close, volume FROM candles WHERE symbol='BTCUSDT' AND timeframe='15m' AND market_type='spot' ORDER BY open_time DESC LIMIT 150"
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
        conn.close()
        if not rows: return None
        
        rows = list(reversed(rows))
        df = pd.DataFrame(rows, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['Date'] = pd.to_datetime(df['Date'], utc=True)
        df = df.set_index('Date')
        return df.astype(float)
    except Exception as e:
        log.error(f"DB Error: {e}")
        return None

def draw_chart(event):
    try:
        deal_type = event.get("deal_type", "LONG")
        df = get_db_candles()
        if df is None or df.empty: return None

        df_1h = df.resample('1h').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        }).dropna()
        
        df = df_1h.tail(40).copy() 
        
        entry_price = float(event.get("entry", 0))
        
        if event["type"] == "ENTRY":
            sl_price = float(event["sl"])
            tp1_price = float(event.get("tp1", 0))
            tp2_price = float(event.get("tp2", 0))
            tp_price = float(event.get("tp", tp2_price or tp1_price))
            title = f"Вход {deal_type}"
        else:
            exit_price = float(event.get("exit", entry_price))
            title = f"Выход {deal_type}"

        exp12 = df['Close'].ewm(span=12, adjust=False).mean()
        exp26 = df['Close'].ewm(span=26, adjust=False).mean()
        macd_line = exp12 - exp26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        macd_colors = ['#26a69a' if val >= 0 else '#ef5350' for val in macd_hist]

        last_close = df['Close'].iloc[-1]
        last_open = df['Open'].iloc[-1]
        last_color = '#26a69a' if last_close >= last_open else '#ef5350'

        max_list = [df['High'].max(), entry_price]
        min_list = [df['Low'].min(), entry_price]
        if event["type"] == "ENTRY":
            max_list.extend([tp1_price, tp2_price])
            min_list.extend([tp1_price, tp2_price, sl_price])
        else:
            max_list.append(exit_price)
            min_list.append(exit_price)

        max_price = max(max_list)
        min_price = min(min_list)
        padding = (max_price - min_price) * 0.15
        ymin, ymax = min_price - padding, max_price + padding

        rc = {'axes.labelsize': 14, 'ytick.labelsize': 16, 'xtick.labelsize': 14, 'axes.titlesize': 20, 'figure.titlesize': 20}
        mc = mpf.make_marketcolors(up='#26a69a', down='#ef5350', edge='inherit', wick='inherit', volume='in')
        s = mpf.make_mpf_style(marketcolors=mc, base_mpf_style='nightclouds', facecolor='#131722', figcolor='#131722', gridcolor='#2a2e39', gridstyle='--', rc=rc, y_on_right=True)

        apds = [
            mpf.make_addplot(macd_hist, type='bar', color=macd_colors, panel=2),
            mpf.make_addplot(macd_line, color='#FFD700', panel=2, width=1),
            mpf.make_addplot(signal_line, color='#9370DB', panel=2, width=1)
        ]

        hlines_list = [entry_price]
        hlines_colors = ['blue']

        if event["type"] == "ENTRY":
            hlines_list.extend([sl_price, tp1_price, tp2_price])
            hlines_colors.extend(['#ef5350', '#26a69a', '#26a69a'])
        else:
            hlines_list.append(exit_price)
            hlines_colors.append('orange')

        fig, axes = mpf.plot(
            df, type='candle', style=s, ylabel='', ylabel_lower='', volume=True, addplot=apds,
            panel_ratios=(7, 1.5, 3), figsize=(14, 10), ylim=(ymin, ymax),
            hlines=dict(hlines=hlines_list, colors=hlines_colors, linestyle='--', linewidths=[1.5]*len(hlines_list)),
            returnfig=True, tight_layout=True,
            xlim=(0, len(df) + 5) 
        )

        axes[0].set_title(f"BTC/USDT | 1H | {title}", fontsize=20, fontweight='bold', pad=15)
        ax_main = axes[0]
        fig.text(0.5, 0.5, 'ARGUS', alpha=0.1, fontsize=100, ha='center', va='center', color='white', rotation=30)

        font_size_label = 20
        bbox_style = 'round,pad=0.6'
        
        if event["type"] == "ENTRY":
            ax_main.text(0.01, entry_price, f' ВХОД: {entry_price:,.2f} ', color='white', fontsize=font_size_label, fontweight='bold', va='center', ha='left', bbox=dict(facecolor='blue', alpha=0.9, edgecolor='none', boxstyle=bbox_style), transform=ax_main.get_yaxis_transform())
            ax_main.text(0.01, sl_price, f' СТОП: {sl_price:,.2f} ', color='white', fontsize=font_size_label, fontweight='bold', va='center', ha='left', bbox=dict(facecolor='#ef5350', alpha=0.9, edgecolor='none', boxstyle=bbox_style), transform=ax_main.get_yaxis_transform())
            ax_main.text(0.01, tp1_price, f' ТЕЙК 1: {tp1_price:,.2f} ', color='white', fontsize=font_size_label, fontweight='bold', va='center', ha='left', bbox=dict(facecolor='#26a69a', alpha=0.9, edgecolor='none', boxstyle=bbox_style), transform=ax_main.get_yaxis_transform())
            ax_main.text(0.01, tp2_price, f' ТЕЙК 2: {tp2_price:,.2f} ', color='white', fontsize=font_size_label, fontweight='bold', va='center', ha='left', bbox=dict(facecolor='#26a69a', alpha=0.9, edgecolor='none', boxstyle=bbox_style), transform=ax_main.get_yaxis_transform())
        else:
            ax_main.text(0.01, entry_price, f' ВХОД: {entry_price:,.2f} ', color='white', fontsize=font_size_label, fontweight='bold', va='center', ha='left', bbox=dict(facecolor='blue', alpha=0.9, edgecolor='none', boxstyle=bbox_style), transform=ax_main.get_yaxis_transform())
            ax_main.text(0.01, exit_price, f' ВЫХОД: {exit_price:,.2f} ', color='white', fontsize=font_size_label, fontweight='bold', va='center', ha='left', bbox=dict(facecolor='orange', alpha=0.9, edgecolor='none', boxstyle=bbox_style), transform=ax_main.get_yaxis_transform())

        ax_main.annotate(
            f' {last_close:,.2f} ', xy=(1.0, last_close), xycoords=('axes fraction', 'data'),
            xytext=(3, 0), textcoords='offset points', color='black',
            bbox=dict(boxstyle="round,pad=0.5", fc=last_color, ec=last_color, lw=1),
            fontsize=14, fontweight='bold', va='center', ha='left'
        )

        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=150, facecolor=fig.get_facecolor(), bbox_inches='tight', pad_inches=0.2)
        buf.seek(0)
        plt.close(fig)
        return buf.getvalue()
    except Exception as e:
        log.error(f"CHART ERROR: {e}")
        return None

def get_stats_text(mode):
    info = load_json(BOT_INFO, {}).get(mode, {})
    closed = [t for t in load_json(CLOSED_DEALS, []) if t.get("mode") == mode]
    
    total = len(closed)
    wins = sum(1 for t in closed if t.get("pnl_usd", 0) > 0)
    losses = sum(1 for t in closed if t.get("pnl_usd", 0) < 0)
    breakeven = total - wins - losses
    
    real_trades = wins + losses
    winrate = (wins / real_trades * 100) if real_trades > 0 else 0
    
    balance = info.get("balance", 100.0)
    total_pnl = balance - info.get("start_balance", 100.0)
    pnl_sign = "+" if total_pnl >= 0 else ""
    
    text = (f"────────────────────\n"
            f"📊 <b>Статистика {mode.upper()}:</b>\n"
            f"📈 Сделок: {total} ({wins} ✅ / {losses} ❌")
    
    if breakeven > 0:
        text += f" / {breakeven} ⚖️"
        
    text += (f")\n"
             f"🎯 Винрейт: <b>{winrate:.1f}%</b>\n"
             f"💰 Баланс: <b>${balance:.2f}</b> (PnL: <b>{pnl_sign}${total_pnl:.2f}</b>)")
             
    return text

def main():
    log.info("STARTING SIGNAL REPORT v8.8.0 (personal DM)")
    if not CHAT_ID:
        log.error("TELEGRAM_CHAT_ID не задан — личка не настроена")
    queue = load_json(INFO_DEAL, [])
    if not isinstance(queue, list) or not queue:
        return log.info("No new events in queue.")
        
    sent_any = False
    for event in queue:
        mode_name = "⚡ ИНТРАДЕЙ" if event.get("mode") == "intraday" else "🌊 СВИНГ"
        deal_type = event.get("deal_type", "LONG")
        text = ""
        
        if event["type"] == "ENTRY":
            entry, sl = float(event["entry"]), float(event["sl"])
            tp1 = float(event.get("tp1", 0))
            tp2 = float(event.get("tp2", 0))
            tp = float(event.get("tp", tp2 or tp1))
            action = "ПОКУПКА" if deal_type == "LONG" else "ПРОДАЖА"
            long_side = deal_type == "LONG"
            pct = lambda v: ((v - entry) / entry * 100) if long_side else ((entry - v) / entry * 100)
            text = f"🟢 <b>ОТКРЫТА СДЕЛКА: {mode_name}</b>\n\n<b>{action} BTC ({deal_type})</b>\n🟢 <b>Вход:</b> {fmt(entry)}\n"
            if tp1 and tp2:
                text += f"🎯 <b>Тейк 1:</b> {fmt(tp1)} ({pct(tp1):+.2f}%)\n"
                text += f"🎯 <b>Тейк 2:</b> {fmt(tp2)} ({pct(tp2):+.2f}%)\n"
            else:
                text += f"🎯 <b>Тейк:</b> {fmt(tp)} ({pct(tp):+.2f}%)\n"
            text += f"🛑 <b>Стоп:</b> {fmt(sl)} ({pct(sl):+.2f}%)\n"
            text += "\n" + get_stats_text(event.get("mode", "swing"))

        elif event["type"] == "UPDATE":
            sl_pct_val = event.get("sl_pct", 0)
            text = (f"🟡 <b>ПОДТЯЖКА СТОПа: {mode_name}</b>\n\n"
                    f"<b>Сделка {deal_type} все еще в работе!</b>\n"
                    f"🛑 <b>Новый стоп:</b> {fmt(event['new_sl'])} ({sl_pct_val:+.2f}%)\n")
            if send_to_all(None, text): sent_any = True
            continue 

        elif event["type"] == "CLOSE":
            pnl = event["pnl_usd"]
            
            pnl_pct_val = event["pnl_pct"]
            if abs(pnl_pct_val) < 0.05:
                pnl_pct_str = "0.00"
                pnl_usd_str = "0.00"
            else:
                pnl_pct_str = f"{pnl_pct_val:+.2f}"
                pnl_usd_str = f"{pnl:+.2f}"

            if pnl > 0: status = "✅ УСПЕХ"
            elif pnl < 0: status = "❌ УБЫТОК"
            else: status = "⚖️ БЕЗУБЫТОК"
                
            reason_raw = event.get("reason", "UNKNOWN")
            reasons_ru = {
                "STOP_LOSS": "Сработал стоп-лосс",
                "TAKE_PROFIT": "Достигнут тейк-профит",
                "TAKE_PROFIT_1": "Частичная фиксация (тейк 1)",
                "TAKE_PROFIT_2": "Достигнут тейк-профит 2",
                "TRAILING_STOP": "Сработал трейлинг-стоп",
                "TIME_EXIT_DAY": "Закрытие по времени (конец дня)",
                "TIME_EXIT_FLAT": "Закрытие по времени (флэт)",
                "TIME_EXIT": "Закрытие по времени"
            }
            reason_text = reasons_ru.get(reason_raw, reason_raw)
            
            entry_price_text = float(event.get("entry", 0))
            exit_price_text = float(event.get("exit", 0))
            
            text = (f"🔴 <b>СДЕЛКА ЗАКРЫТА: {mode_name}</b>\n\n"
                    f"<b>Результат ({deal_type}): {status}</b>\n"
                    f"📥 <b>Вход:</b> {fmt(entry_price_text)}\n"
                    f"📤 <b>Выход:</b> {fmt(exit_price_text)}\n"
                    f"📊 <b>Причина:</b> {reason_text}\n"
                    f"💰 <b>PnL:</b> {pnl_pct_str}% (${pnl_usd_str})\n"
                    f"💼 <b>Баланс:</b> ${event.get('new_balance', 0):.2f}\n")
            text += "\n" + get_stats_text(event.get("mode", "swing"))

        chart = draw_chart(event)
        if send_to_all(chart, text): sent_any = True
        if sent_any:
            log.info(f"Sent {event['type']} ({event.get('mode')}) to PERSONAL DM.")
        
    if sent_any:
        save_json(INFO_DEAL, [])
        log.info("Queue cleared.")

if __name__ == "__main__":
    main()
