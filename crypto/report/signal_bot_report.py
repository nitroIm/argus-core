# ============================================================
# ARGUS - SIGNAL REPORT v7.1 (FIXED CHARTS & STATUS)
# Path: crypto/report/signal_bot_report.py
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
CHAT_ID = (os.getenv("TG_CHANNEL_ID") or "-1004435496321").strip()

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
    r = requests.post(url, data=data, files=files, timeout=30)
    if r.status_code != 200: log.error(f"TG Photo Error: {r.text}")
    return r.status_code == 200

def send_tg_text(text):
    if not BOT_TOKEN or not CHAT_ID: return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=20)
    return r.status_code == 200

def fmt(p): return "$" + format(int(p), ",") if p >= 1000 else "$" + format(p, ".2f")

def get_db_candles(start_time_str, end_time_str=None):
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
        df = df.set_index('Date')
        df = df.astype(float)
        return df
    except Exception as e:
        log.error(f"DB Error: {e}")
        return None

def draw_chart(event):
    try:
        mode = event.get("mode", "swing")
        deal_type = event.get("deal_type", "LONG")
        
        if event["type"] == "ENTRY":
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(hours=24)
            df = get_db_candles(start_time.isoformat(), end_time.isoformat())
            title = f"Вход {deal_type}"
        else:
            open_time = event.get("open_time")
            if not open_time: 
                log.error("Нет open_time для графика")
                return None
            start_dt = datetime.fromisoformat(open_time) - timedelta(hours=12)
            end_dt = datetime.now(timezone.utc) + timedelta(hours=2)
            df = get_db_candles(start_dt.isoformat(), end_dt.isoformat())
            title = f"Выход {deal_type}"

        if df is None or df.empty:
            log.error("Нет данных свечей для графика")
            return None

        df_1h = df.resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
        
        # Если сделка была короткой, свечей может быть мало. Берем сколько есть, но не меньше 3.
        if len(df_1h) < 3:
            log.error("Слишком мало данных для графика")
            return None
            
        df = df_1h.tail(45).copy()

        entry_price = float(event.get("entry", 0))
        if event["type"] == "ENTRY":
            sl_price = float(event["sl"])
            tp_price = float(event["tp"])
        else:
            exit_price = float(event.get("exit", 0))

        exp12 = df['close'].ewm(span=12, adjust=False).mean()
        exp26 = df['close'].ewm(span=26, adjust=False).mean()
        macd_line = exp12 - exp26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        macd_colors = ['#26a69a' if val >= 0 else '#ef5350' for val in macd_hist]

        last_close = df['close'].iloc[-1]
        last_open = df['open'].iloc[-1]
        last_color = '#26a69a' if last_close >= last_open else '#ef5350'

        max_list = [df['high'].max(), entry_price]
        min_list = [df['low'].min(), entry_price]
        if event["type"] == "ENTRY":
            max_list.append(tp_price)
            min_list.append(sl_price)
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
            hlines_list.extend([sl_price, tp_price])
            hlines_colors.extend(['#ef5350', '#26a69a'])
            marker_type = '^' if deal_type == "LONG" else 'v'
            marker_color = '#26a69a' if deal_type == "LONG" else '#ef5350'
            
            open_dt = datetime.now(timezone.utc) if "open_time" not in event else datetime.fromisoformat(event["open_time"])
            closest_idx = df.index.get_indexer([open_dt], method='nearest')[0]
            
            scatter = pd.Series(np.nan, index=df.index)
            scatter.iloc[closest_idx] = entry_price
            apds.append(mpf.make_addplot(scatter, type='scatter', markersize=350, marker=marker_type, color=marker_color, edgecolors='white', linewidths=1.5, panel=0))
        else:
            hlines_list.append(exit_price)
            hlines_colors.append('orange')
            open_dt = datetime.fromisoformat(event["open_time"])
            closest_entry_idx = df.index.get_indexer([open_dt], method='nearest')[0]
            
            scatter_entry = pd.Series(np.nan, index=df.index)
            scatter_entry.iloc[closest_entry_idx] = entry_price
            marker_entry = '^' if deal_type == "LONG" else 'v'
            apds.append(mpf.make_addplot(scatter_entry, type='scatter', markersize=350, marker=marker_entry, color='#26a69a', edgecolors='white', linewidths=1.5, panel=0))

            scatter_exit = pd.Series(np.nan, index=df.index)
            scatter_exit.iloc[-1] = exit_price 
            marker_exit = 'v' if deal_type == "LONG" else '^'
            apds.append(mpf.make_addplot(scatter_exit, type='scatter', markersize=350, marker=marker_exit, color='#ef5350', edgecolors='white', linewidths=1.5, panel=0))

        fig, axes = mpf.plot(
            df, type='candle', style=s, ylabel='', ylabel_lower='', volume=True, addplot=apds,
            panel_ratios=(7, 1.5, 3), figsize=(14, 10), ylim=(ymin, ymax),
            hlines=dict(hlines=hlines_list, colors=hlines_colors, linestyle='--', linewidths=[1.5]*len(hlines_list)),
            returnfig=True, tight_layout=True
        )

        axes[0].set_title(f"BTC/USDT | 1H | {title}", fontsize=20, fontweight='bold', pad=15)
        ax_main = axes[0]
        fig.text(0.5, 0.5, 'ARGUS', alpha=0.1, fontsize=100, ha='center', va='center', color='white', rotation=30)

        if event["type"] == "ENTRY":
            ax_main.text(0.01, entry_price, ' ВХОД', color='white', fontsize=12, fontweight='bold', va='center', ha='left', bbox=dict(facecolor='blue', alpha=0.8, edgecolor='none', boxstyle='round,pad=0.2'), transform=ax_main.get_yaxis_transform())
            ax_main.text(0.01, sl_price, ' СТОП', color='white', fontsize=12, fontweight='bold', va='center', ha='left', bbox=dict(facecolor='#ef5350', alpha=0.8, edgecolor='none', boxstyle='round,pad=0.2'), transform=ax_main.get_yaxis_transform())
            ax_main.text(0.01, tp_price, ' ТЕЙК', color='white', fontsize=12, fontweight='bold', va='center', ha='left', bbox=dict(facecolor='#26a69a', alpha=0.8, edgecolor='none', boxstyle='round,pad=0.2'), transform=ax_main.get_yaxis_transform())
        else:
            ax_main.text(0.01, entry_price, ' ВХОД', color='white', fontsize=12, fontweight='bold', va='center', ha='left', bbox=dict(facecolor='blue', alpha=0.8, edgecolor='none', boxstyle='round,pad=0.2'), transform=ax_main.get_yaxis_transform())
            ax_main.text(0.01, exit_price, ' ВЫХОД', color='white', fontsize=12, fontweight='bold', va='center', ha='left', bbox=dict(facecolor='orange', alpha=0.8, edgecolor='none', boxstyle='round,pad=0.2'), transform=ax_main.get_yaxis_transform())

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
        log.error(f"Ошибка при отрисовке графика: {e}")
        return None # Если график упал, отправим хотя бы текст

def get_stats_text(mode):
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
    log.info("STARTING SIGNAL REPORT v7.1")
    queue = load_json(INFO_DEAL, [])
    if not isinstance(queue, list) or not queue:
        return log.info("No new events in queue.")
        
    sent_any = False
    for event in queue:
        mode_name = "⚡ ИНТРАДЕЙ" if event.get("mode") == "intraday" else "🌊 СВИНГ"
        deal_type = event.get("deal_type", "LONG")
        text = ""
        
        if event["type"] == "ENTRY":
            entry, tp, sl = float(event["entry"]), float(event["tp"]), float(event["sl"])
            action = "ПОКУПКА" if deal_type == "LONG" else "ПРОДАЖА"
            text = f"🟢 <b>ОТКРЫТА СДЕЛКА: {mode_name}</b>\n\n<b>{action} BTC ({deal_type})</b>\n🟢 <b>Вход:</b> {fmt(entry)}\n"
            if deal_type == "LONG":
                text += f"🎯 <b>Тейк:</b> {fmt(tp)} ({(tp-entry)/entry*100:+.2f}%)\n🛑 <b>Стоп:</b> {fmt(sl)} ({(sl-entry)/entry*100:+.2f}%)\n"
            else:
                text += f"🎯 <b>Тейк:</b> {fmt(tp)} ({(entry-tp)/entry*100:+.2f}%)\n🛑 <b>Стоп:</b> {fmt(sl)} ({(entry-sl)/entry*100:+.2f}%)\n"
            text += "\n" + get_stats_text(event.get("mode", "swing"))

        elif event["type"] == "UPDATE":
            # Убрал строку "Бот защищает профит"
            text = (f"🟡 <b>ПОДТЯЖКА СТОПА: {mode_name}</b>\n\n"
                    f"<b>Сделка {deal_type} все еще в работе!</b>\n"
                    f"🛑 <b>Новый стоп:</b> {fmt(event['new_sl'])} ({event['sl_pct']:+.2f}%)\n")
            if send_tg_text(text): sent_any = True
            continue 

        elif event["type"] == "CLOSE":
            pnl = event["pnl_usd"]
            sign = "+" if pnl > 0 else ""
            # Если 0, пишем БЕЗУБЫТОК
            if pnl > 0: status = "✅ УСПЕХ"
            elif pnl < 0: status = "❌ УБЫТОК"
            else: status = "⚖️ БЕЗУБЫТОК"
                
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
            log.info(f"Sent {event['type']} ({event.get('mode')}) to PRIVATE CHANNEL.")
        
    if sent_any:
        save_json(INFO_DEAL, [])
        log.info("Queue cleared.")

if __name__ == "__main__":
    main()