# ============================================================
# ARGUS - MORNING REPORT v16.0.1 (BARS & LIVE CMC)
# ------------------------------------------------------------
# v16.0.1: Возвращены визуальные шкалы (▕██░░▏) для цены и объемов.
# ============================================================

import os
import sys
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

try:
    import psycopg
except ImportError:
    print("ERR: psycopg not installed")
    sys.exit(1)

TZ = ZoneInfo("Europe/Kaliningrad")
DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
CMC_API_KEY = (os.getenv("CMC_API_KEY") or "").strip()

SYMBOL = "BTCUSDT"
TIMEFRAME = "15m"
H24 = 96

def now_local(): return datetime.now(timezone.utc).astimezone(TZ)

def fmt_price(p):
    if p is None: return "?"
    if p >= 1000: return "$" + format(int(p), ",")
    if p >= 1: return "$" + format(p, ".2f")
    return "$" + format(p, ".4f")

def fmt_usd(v):
    if v is None: return "?"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1_000_000_000_000: return sign + "$" + format(a / 1e12, ".2f") + "T"
    if a >= 1_000_000_000: return sign + "$" + format(a / 1e9, ".2f") + "B"
    if a >= 1_000_000: return sign + "$" + format(a / 1e6, ".2f") + "M"
    if a >= 1_000: return sign + "$" + format(a / 1e3, ".1f") + "K"
    return sign + "$" + format(a, ".2f")

def fmt_btc(v):
    if v is None: return "?"
    if v >= 1000: return format(v, ",.1f") + " BTC"
    if v >= 1: return format(v, ".2f") + " BTC"
    return format(v, ".4f") + " BTC"

def fmt_pct(v): return format(v, "+.2f") + "%" if v is not None else "?"

def fmt_age(mins):
    if mins is None: return "?"
    if mins < 60: return str(int(mins)) + " мин"
    if mins < 1440: return str(int(mins // 60)) + " ч"
    return str(int(mins // 1440)) + " д"

def _to_dt(ts):
    if ts is None: return None
    if ts.tzinfo is None: return ts.replace(tzinfo=timezone.utc)
    return ts

def _to_local(ts):
    ts = _to_dt(ts)
    if ts is None: return "?"
    return ts.astimezone(TZ).strftime("%d.%m %H:%M")

def get_conn():
    if not DB_URL: raise RuntimeError("AIVEN_DB_URL not set")
    return psycopg.connect(DB_URL, connect_timeout=30, application_name="argus-morning")

def ema_series(values, period):
    if len(values) < period: return [None] * len(values)
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    out = [None] * (period - 1) + [e]
    for i in range(period, len(values)):
        e = values[i] * k + e * (1 - k)
        out.append(e)
    return out

def rsi_series(closes, period=14):
    if len(closes) < period + 1: return [None] * len(closes)
    out = [None] * period
    gains = 0.0; losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0: gains += d
        else: losses -= d
    ag = gains / period; al = losses / period
    rs = ag / al if al > 0 else 999
    out.append(100 - 100 / (1 + rs))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        g = d if d > 0 else 0; l = -d if d < 0 else 0
        ag = (ag * (period - 1) + g) / period
        al = (al * (period - 1) + l) / period
        rs = ag / al if al > 0 else 999
        out.append(100 - 100 / (1 + rs))
    return out

def atr_value(candles, period=14):
    if len(candles) < period + 1: return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]; l = candles[i]["low"]; pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period: return None
    a = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
    return a

def fetch_candles(symbol, limit=300):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT open_time, open, high, low, close, volume, taker_buy_volume, quote_volume, count "
                    "FROM candles WHERE symbol=%s AND timeframe=%s AND market_type='spot' "
                    "ORDER BY open_time DESC LIMIT %s", (symbol, TIMEFRAME, limit)
                )
                rows = list(reversed(cur.fetchall()))
                return [{
                    "timestamp": r[0], "open": float(r[1]), "high": float(r[2]),
                    "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]) if r[5] else 0.0,
                    "taker_buy_volume": float(r[6]) if r[6] else 0.0,
                    "quote_volume": float(r[7]) if r[7] else 0.0, "count": int(r[8]) if r[8] else 0
                } for r in rows]
    except Exception as e:
        print("candles: " + str(e))
        return []

def fetch_stats(symbol):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM candles WHERE symbol=%s", (symbol,))
                total = cur.fetchone()[0] or 0
                cur.execute("SELECT market_type, COUNT(*) FROM candles WHERE symbol=%s GROUP BY market_type", (symbol,))
                by_market = dict(cur.fetchall())
                cur.execute("SELECT MAX(open_time) FROM candles WHERE symbol=%s", (symbol,))
                last_ts = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM candles WHERE symbol=%s AND open_time > NOW() - INTERVAL '24 hours'", (symbol,))
                rows_24h = cur.fetchone()[0] or 0
        return {"total": total, "by_market": by_market, "last_ts": last_ts, "rows_24h": rows_24h}
    except Exception as e:
        print("stats: " + str(e))
        return None

def fetch_live_cmc():
    if not CMC_API_KEY: return None
    url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json().get("data", {})
            quote_usd = data.get("quote", {}).get("USD", {})
            return {
                "btc_dominance": float(data.get("btc_dominance", 0)),
                "total_market_cap": float(quote_usd.get("total_market_cap", 0)),
                "stablecoin_market_cap": float(quote_usd.get("stablecoin_market_cap", 0))
            }
    except Exception: pass
    return None

def fetch_prev_global_metrics(hours_ago=24):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT btc_dominance, total_market_cap, stablecoin_market_cap FROM global_metrics WHERE last_updated <= NOW() - INTERVAL '{hours_ago} hours' ORDER BY last_updated DESC LIMIT 1")
                prev_row = cur.fetchone()
        if not prev_row: return None
        return {"btc_dominance": float(prev_row[0]), "total_market_cap": float(prev_row[1]), "stablecoin_market_cap": float(prev_row[2])}
    except Exception: return None

def fetch_network_sentiment():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value, classification FROM fear_greed ORDER BY timestamp DESC LIMIT 1")
                fng_cur = cur.fetchone()
                cur.execute("SELECT value FROM fear_greed WHERE timestamp <= NOW() - INTERVAL '23 hours' ORDER BY timestamp DESC LIMIT 1")
                fng_prev = cur.fetchone()
                cur.execute("SELECT chart_name, value FROM btc_network WHERE chart_name IN ('hash-rate', 'difficulty', 'miners-revenue') ORDER BY chart_time DESC LIMIT 3")
                net_cur = dict(cur.fetchall())
                cur.execute("SELECT chart_name, value FROM btc_network WHERE chart_name IN ('hash-rate', 'difficulty', 'miners-revenue') AND chart_time <= NOW() - INTERVAL '23 hours' ORDER BY chart_time DESC LIMIT 3")
                net_prev = dict(cur.fetchall())
        return {"fng": (fng_cur, fng_prev), "network": (net_cur, net_prev)}
    except Exception: return None

def fetch_stablecoins():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT symbol, price FROM stablecoins WHERE symbol IN ('USDT', 'USDC', 'DAI') ORDER BY timestamp DESC LIMIT 3")
                return dict(cur.fetchall())
    except Exception: return {}

def compute_period(candles):
    if not candles: return None
    first = candles[0]; last = candles[-1]
    price = last["close"]; open_p = first["open"]
    change_usd = price - open_p
    change_pct = (change_usd / open_p * 100) if open_p else 0
    hi_c = max(candles, key=lambda c: c["high"])
    hi = hi_c["high"]; hi_ts = hi_c["timestamp"]
    hi_pct = (hi - open_p) / open_p * 100 if open_p else 0
    lo_c = min(candles, key=lambda c: c["low"])
    lo = lo_c["low"]; lo_ts = lo_c["timestamp"]
    lo_pct = (lo - open_p) / open_p * 100 if open_p else 0
    vol_btc = sum(c["volume"] for c in candles)
    vol_usd = sum(c["quote_volume"] for c in candles)
    taker_buy = sum(c["taker_buy_volume"] for c in candles)
    taker_sell = vol_btc - taker_buy
    buy_pct = (taker_buy / vol_btc * 100) if vol_btc else 0
    sell_pct = 100 - buy_pct if vol_btc else 0
    trades = sum(c["count"] for c in candles)
    avg_trade_usd = (vol_usd / trades) if trades else 0
    avg_trade_btc = (vol_btc / trades) if trades else 0
    green = sum(1 for c in candles if c["close"] >= c["open"])
    red = len(candles) - green
    return {
        "open": open_p, "close": price, "change_usd": change_usd, "change_pct": change_pct,
        "hi": hi, "hi_pct": hi_pct, "hi_ts": hi_ts, "lo": lo, "lo_pct": lo_pct, "lo_ts": lo_ts,
        "vol_btc": vol_btc, "vol_usd": vol_usd, "taker_buy": taker_buy, "taker_sell": taker_sell,
        "buy_pct": buy_pct, "sell_pct": sell_pct, "trades": trades, "avg_trade_usd": avg_trade_usd,
        "avg_trade_btc": avg_trade_btc, "green": green, "red": red, "count": len(candles),
    }

def calc_delta(cur, prev, is_pct=False, is_money=False):
    if prev is None or cur is None: return "(нет данных)"
    if prev == 0: return "(⏸️ 0)"
    diff = cur - prev
    sign = "🔼" if diff > 0 else "🔽" if diff < 0 else "⏸️"
    if diff == 0: return f"({sign} 0)"
    if is_pct: return f"({sign} {diff:+.2f}%)"
    if is_money: return f"({sign} {fmt_usd(diff)})"
    return f"({sign} {diff:+g})"

# ============================================================
# ВИЗУАЛЬНЫЕ ШКАЛЫ
# ============================================================
def get_progress_bar(pct, length=12):
    filled = int(round(pct / 100 * length))
    filled = max(0, min(filled, length))
    return "▕" + "█" * filled + "░" * (length - filled) + "▏"

def get_range_bar(price, low, high, length=12):
    if high == low: return "▕░░░░░░░░░░░░▏"
    pct = (price - low) / (high - low) * 100
    filled = int(round(pct / 100 * length))
    filled = max(0, min(filled, length))
    return "▕" + "█" * filled + "░" * (length - filled) + "▏"

# ============================================================
# TEXT BLOCKS
# ============================================================
def fmt_collector_block(stats):
    lines = ["📡 <b>Сбор данных</b>"]
    if stats is None:
        lines.append("  ❌ DB недоступна")
        return lines
    last_ts = stats["last_ts"]
    if last_ts is None: lines.append("  Статус: ❌ нет данных")
    else:
        age_min = int((datetime.now(timezone.utc) - _to_dt(last_ts)).total_seconds() / 60)
        if age_min <= 30: lines.append("  Статус: 🟢 ON")
        elif age_min <= 120: lines.append("  Статус: ⚠️ задержка")
        else: lines.append("  Статус: 🔴 OFF")
    lines.append("  Свечей всего: " + str(stats["total"]))
    if stats["by_market"]:
        lines.append("  " + " | ".join([f"{mt} {cnt}" for mt, cnt in sorted(stats["by_market"].items())]))
    lines.append("  За 24ч: " + str(stats["rows_24h"]))
    if last_ts:
        age_min = int((datetime.now(timezone.utc) - _to_dt(last_ts)).total_seconds() / 60)
        lines.append("  Последняя: " + _to_local(last_ts) + " (" + fmt_age(age_min) + " назад)")
    return lines

def fmt_day_block(s):
    if not s: return ["  нет данных за день"]
    
    bar_price = get_range_bar(s["close"], s["lo"], s["hi"])
    
    sign = "+" if s["change_usd"] >= 0 else "-"
    line1 = f"  {fmt_price(s['open'])} → {fmt_price(s['close'])} | {fmt_pct(s['change_pct'])} ({sign}{fmt_price(abs(s['change_usd']))})"
    line2 = f"  Мин <code>{fmt_price(s['lo'])}</code> {bar_price} Макс <code>{fmt_price(s['hi'])}</code>"
    line3 = f"  Свечей: {s['count']} (🟢 {s['green']} / 🔴 {s['red']})"
    return [line1, line2, line3]

def fmt_volume_block(s):
    if not s: return ["  нет данных"]
    
    bar_buy = get_progress_bar(s["buy_pct"])
    bar_sell = get_progress_bar(s["sell_pct"])
    
    return [
        f"  Объём: {fmt_btc(s['vol_btc'])} | {fmt_usd(s['vol_usd'])}",
        f"  🟢 Покупки: {format(s['buy_pct'], '.1f')}%",
        f"  {bar_buy}",
        f"  🔴 Продажи: {format(s['sell_pct'], '.1f')}%",
        f"  {bar_sell}",
        f"  Сделок: {format(s['trades'], ',')}",
        f"  Средняя сделка: {fmt_usd(s['avg_trade_usd'])}"
    ]

def fmt_global_market(live_data, prev_data, stables):
    lines = []
    if live_data:
        btc_dom = live_data["btc_dominance"]
        mcap = live_data["total_market_cap"]
        stbl_mcap = live_data["stablecoin_market_cap"]
    else:
        lines.append("  ⚠️ CMC не отвечает, данные из кэша")
        return lines

    prev_dom = prev_data["btc_dominance"] if prev_data else None
    d_dom = calc_delta(btc_dom, prev_dom, is_pct=True) if prev_dom else "(нет данных)"
    lines.append(f"  Доминация BTC: {btc_dom:.2f}% {d_dom}")

    prev_mcap = prev_data["total_market_cap"] if prev_data else None
    d_mcap = calc_delta(mcap, prev_mcap, is_money=True) if prev_mcap else "(нет данных)"
    lines.append(f"  Капитализация: {fmt_usd(mcap)} {d_mcap}")

    usdt_price = stables.get("USDT")
    usdt_str = f"USDT: ${usdt_price:.3f} ✅" if usdt_price and 0.99 <= float(usdt_price) <= 1.01 else f"USDT: ${usdt_price:.3f} ⚠️" if usdt_price else "USDT: ?"
    lines.append(f"  Стейблкоины: {fmt_usd(stbl_mcap)} ({usdt_str})")
    return lines

def fmt_network_sentiment(data):
    if not data: return ["  ❌ Данные Сети/Сентимента недоступны"]
    lines = []
    fng_cur, fng_prev = data["fng"]
    if fng_cur:
        val = int(fng_cur[0]); cls = fng_cur[1]
        prev_val = int(fng_prev[0]) if fng_prev else None
        delta = calc_delta(val, prev_val) if prev_val is not None else "(нет данных)"
        lines.append(f"  Страх/Жадность: {val} ({cls}) {delta}")
    else:
        lines.append("  Страх/Жадность: (нет данных)")
    net_cur, net_prev = data["network"]
    if net_cur:
        hr = net_cur.get("hash-rate")
        if hr:
            hr_eh = float(hr) / 1e6
            prev_hr = net_prev.get("hash-rate")
            d_hr = calc_delta(hr_eh, float(prev_hr) / 1e6, is_pct=True) if prev_hr else "(нет данных)"
            lines.append(f"  Хешрейт: {hr_eh:.0f} EH/s {d_hr}")
        diff = net_cur.get("difficulty")
        if diff:
            diff_t = float(diff) / 1e12
            prev_diff = net_prev.get("difficulty")
            d_diff = calc_delta(diff_t, float(prev_diff) / 1e12, is_pct=True) if prev_diff else "(нет данных)"
            lines.append(f"  Сложность: {diff_t:.1f} T {d_diff}")
        rev = net_cur.get("miners-revenue")
        if rev:
            rev_f = float(rev)
            prev_rev = net_prev.get("miners-revenue")
            d_rev = calc_delta(rev_f, float(prev_rev), is_pct=True) if prev_rev else "(нет данных)"
            lines.append(f"  Доход майнеров: {fmt_usd(rev_f)} {d_rev}")
    else:
        lines.append("  Сеть BTC: (нет данных)")
    return lines

def fmt_indicators_block(candles):
    if not candles: return ["  нет данных"]
    price = candles[-1]["close"]
    a = atr_value(candles, 14)
    a_pct = (a / price * 100) if a and price else 0
    closes = [c["close"] for c in candles]
    r15 = rsi_series(closes, 14)[-1]
    df_temp = pd.DataFrame([{'timestamp': c['timestamp'], 'close': c['close']} for c in candles])
    df_temp['timestamp'] = pd.to_datetime(df_temp['timestamp'], utc=True)
    df_temp.set_index('timestamp', inplace=True)
    df_1h = df_temp.resample('1h').agg({'close': 'last'}).dropna()
    c1h = df_1h['close'].tolist()
    r1h = rsi_series(c1h, 14)[-1] if c1h and len(c1h) > 14 else None
    e9 = ema_series(closes, 9)[-1]; e21 = ema_series(closes, 21)[-1]; e50 = ema_series(closes, 50)[-1]
    lines = []
    if a: lines.append(f"  ATR(14) 15m: {fmt_price(a)} ({format(a_pct, '.2f')}%)")
    if r15 is not None:
        st = "перекуп" if r15 >= 70 else "перепрод" if r15 <= 30 else "нейтр"
        lines.append(f"  RSI(14) 15m: {format(r15, '.1f')} {st}")
    if r1h is not None:
        st = "перекуп" if r1h >= 70 else "перепрод" if r1h <= 30 else "нейтр"
        lines.append(f"  RSI(14) 1h:  {format(r1h, '.1f')} {st}")
    if e9: lines.append(f"  EMA(9)  15m: {fmt_price(e9)} (цена {'выше' if price >= e9 else 'ниже'})")
    if e21: lines.append(f"  EMA(21) 15m: {fmt_price(e21)} (цена {'выше' if price >= e21 else 'ниже'})")
    if e50: lines.append(f"  EMA(50) 15m: {fmt_price(e50)} (цена {'выше' if price >= e50 else 'ниже'})")
    return lines

def fmt_extremes_block(s):
    if not s: return ["  нет данных"]
    return [
        f"  Максимум: {fmt_price(s['hi'])} ({fmt_pct(s['hi_pct'])}) в {_to_local(s['hi_ts'])} КЛГ",
        f"  Минимум:  {fmt_price(s['lo'])} ({fmt_pct(s['lo_pct'])}) в {_to_local(s['lo_ts'])} КЛГ"
    ]

def split_text(text, max_len=3800):
    if len(text) <= max_len: return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current: parts.append(current)
            current = line
        else: current = current + "\n" + line if current else line
    if current: parts.append(current)
    return parts

def send_message(text):
    if not BOT_TOKEN or not CHAT_ID: return False
    for part in split_text(text, 3800):
        try:
            url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
            r = requests.post(url, json={"chat_id": CHAT_ID, "text": part, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
            if r.status_code != 200: print("send err " + str(r.status_code))
        except Exception as e:
            print("send: " + str(e))
            return False
    return True

def build_report_text(candles, stats, live_cmc, prev_g_metrics, n_sentiment, stables):
    now = now_local()
    lines = ["☀️ <b>ARGUS — утро</b>", now.strftime("%d.%m.%Y %H:%M") + " КЛГ", ""]
    lines.extend(fmt_collector_block(stats))
    lines.extend(["", "─" * 20, ""])
    s24 = compute_period(candles[-H24:]) if len(candles) >= H24 else compute_period(candles)
    lines.append("💰 <b>BTC (spot)</b>")
    lines.extend(fmt_day_block(s24))
    lines.append("")
    lines.append("📊 <b>Объёмы за 24ч</b>")
    lines.extend(fmt_volume_block(s24))
    lines.append("")
    lines.append("🌍 <b>Глобальный рынок</b>")
    lines.extend(fmt_global_market(live_cmc, prev_g_metrics, stables))
    lines.append("")
    lines.append("🌐 <b>Сеть и Сентимент</b>")
    lines.extend(fmt_network_sentiment(n_sentiment))
    lines.append("")
    lines.append("📈 <b>Индикаторы</b>")
    lines.extend(fmt_indicators_block(candles))
    lines.append("")
    lines.append("🕯️ <b>Структура 24ч</b>")
    lines.extend(fmt_extremes_block(s24))
    return "\n".join(lines)

def main():
    print("Morning report v16.0.1 - start")
    stats = fetch_stats(SYMBOL)
    candles = fetch_candles(SYMBOL, 300)
    live_cmc = fetch_live_cmc()
    prev_g_metrics = fetch_prev_global_metrics(hours_ago=24)
    n_sentiment = fetch_network_sentiment()
    stables = fetch_stablecoins()

    text = build_report_text(candles, stats, live_cmc, prev_g_metrics, n_sentiment, stables)
    print("len: " + str(len(text)))
    send_message(text)
    print("done")

if __name__ == "__main__":
    main()