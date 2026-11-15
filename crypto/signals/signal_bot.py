# ============================================================
# ARGUS - SIGNAL BOT v9.4.7
# Path: crypto/signals/signal_bot.py
#
# ИЗМЕНЕНИЯ v9.4.7 (относительно v9.4.6):
#   - Встроен Data Watchdog: если последняя 15m свеча старше 45 мин,
#     бот пишет алерт в Telegram и приостанавливает работу.
#   - Защита от спама: алерт не чаще раза в час.
# ============================================================

import os, json, logging, base64, pickle, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
import psycopg
import pandas as pd
import numpy as np
import xgboost as xgb

VERSION = "9.4.7"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.signal_bot")

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
MODELS_DIR = CRYPTO_ROOT / "data" / "models"
MODEL_FILE = MODELS_DIR / "best_params.json"
SIGNALS_DIR = CRYPTO_ROOT / "data" / "signals"
CONFIG_FILE = CRYPTO_ROOT / "data" / "intel" / "bot_config.json"

SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
OPENED_DEALS = SIGNALS_DIR / "opened_deals.json"
CLOSED_DEALS = SIGNALS_DIR / "closed_deals.json"
INFO_DEAL = SIGNALS_DIR / "info_deal.json"
BOT_INFO = SIGNALS_DIR / "bot_info.json"

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
SYMBOL = "BTCUSDT"
TG_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
TG_CHAT = (os.getenv("TG_CHANNEL_ID") or "-1004435496321").strip()


def get_conn():
    if not DB_URL: raise RuntimeError("AIVEN_DB_URL not set")
    return psycopg.connect(DB_URL, connect_timeout=30)

def load_json(path, default=None):
    if not path.exists(): return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default if default is not None else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def append_event(event):
    queue = load_json(INFO_DEAL, [])
    if not isinstance(queue, list): queue = []
    queue.append(event)
    save_json(INFO_DEAL, queue)

def send_tg_text(text):
    if not TG_TOKEN or not TG_CHAT: return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}, timeout=10)
        return True
    except Exception as e:
        log.error(f"TG alert failed: {e}")
        return False

def check_data_freshness(df_15m, config, bot_info):
    """Проверяет, не завис ли коллектор. Пишет алерт, если данные устарели."""
    if df_15m.empty: return False
    last_time = df_15m['open_time'].iloc[-1]
    now_utc = datetime.now(timezone.utc)
    diff_min = (now_utc - last_time).total_seconds() / 60.0
    limit_min = float(config.get("global", {}).get("stale_data_limit_min", 45.0))
    
    if diff_min > limit_min:
        log.warning(f"DATA STALE: last candle {last_time} ({diff_min:.1f} min ago, limit {limit_min})")
        
        # Защита от спама: алерт не чаще раза в час
        last_alert_str = bot_info.get("last_stale_alert_ts")
        should_alert = True
        if last_alert_str:
            try:
                last_alert = datetime.fromisoformat(last_alert_str)
                if (now_utc - last_alert).total_seconds() < 3600:
                    should_alert = False
            except: pass
            
        if should_alert:
            msg = (f"⚠️ <b>ARGUS: ДАННЫЕ НЕ ПОСТУПАЮТ</b>\n\n"
                   f"Последняя свеча 15m: <b>{last_time.strftime('%Y-%m-%d %H:%M')} UTC</b>\n"
                   f"Прошло: <b>{diff_min:.0f} минут</b> (лимит {limit_min:.0f})\n\n"
                   f"Бот приостановил работу. Проверь сервер сбора данных!")
            send_tg_text(msg)
            bot_info["last_stale_alert_ts"] = now_utc.isoformat()
            save_json(BOT_INFO, bot_info)
            
        return False
    return True

def ensure_bot_info(bot_info):
    for mode in ["intraday", "swing"]:
        if mode not in bot_info: bot_info[mode] = {"score_history": [], "score_archive": []}
        if "started_at" not in bot_info[mode]: bot_info[mode]["started_at"] = datetime.now(timezone.utc).isoformat()
        if "balance" not in bot_info[mode]: bot_info[mode]["balance"] = 100.0
        if "start_balance" not in bot_info[mode]: bot_info[mode]["start_balance"] = 100.0
        if "last_check_ts" not in bot_info[mode]: bot_info[mode]["last_check_ts"] = datetime.now(timezone.utc).isoformat()
    save_json(BOT_INFO, bot_info)
    return bot_info

def calculate_indicators(df_1h):
    close = df_1h['close']
    df_1h['rsi'] = 100 - 100 / (1 + close.diff().where(close.diff() > 0, 0).rolling(14).mean() / (-close.diff().where(close.diff() < 0, 0)).rolling(14).mean())
    df_1h['rsi_7'] = 100 - 100 / (1 + close.diff().where(close.diff() > 0, 0).rolling(7).mean() / (-close.diff().where(close.diff() < 0, 0)).rolling(7).mean())
    df_1h['rsi_21'] = 100 - 100 / (1 + close.diff().where(close.diff() > 0, 0).rolling(21).mean() / (-close.diff().where(close.diff() < 0, 0)).rolling(21).mean())
    df_1h['ema_20'] = close.ewm(span=20).mean()
    df_1h['ema_50'] = close.ewm(span=50).mean()
    df_1h['ema_200'] = close.ewm(span=200).mean()
    macd = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    df_1h['macd_hist'] = macd - macd.ewm(span=9).mean()
    df_1h['vol_sma'] = df_1h['volume'].rolling(20).mean()
    df_1h['atr_7'] = (df_1h['high'] - df_1h['low']).rolling(7).mean()
    df_1h['atr_14'] = (df_1h['high'] - df_1h['low']).rolling(14).mean()
    df_1h['atr_50'] = (df_1h['high'] - df_1h['low']).rolling(50).mean()
    df_1h['atr_100'] = (df_1h['high'] - df_1h['low']).rolling(100).mean()
    df_1h['buy_ratio'] = df_1h['taker_buy_volume'] / df_1h['volume'].replace(0, 1)
    df_1h['avg_trade_size'] = df_1h['volume'] / df_1h['count'].replace(0, 1)
    df_1h['price_change_1h'] = close.pct_change(1)
    df_1h['price_change_4h'] = close.pct_change(4)
    df_1h['price_change_24h'] = close.pct_change(24)
    return df_1h.dropna()

def fetch_macro_for_bot():
    if not DB_URL: return pd.DataFrame()
    try:
        conn = get_conn()
        frames = []
        df_mi = pd.read_sql("SELECT ticker, open_time AS ts, close FROM market_indices WHERE open_time > NOW() - INTERVAL '7 days' ORDER BY open_time ASC", conn)
        if not df_mi.empty:
            df_mi['ts'] = pd.to_datetime(df_mi['ts'], utc=True)
            for t, c in {'^IXIC':'nasdaq','DX-Y.NYB':'dxy'}.items():
                sub = df_mi[df_mi['ticker']==t][['ts','close']].copy()
                if sub.empty: continue
                frames.append(sub.rename(columns={'close':c}).sort_values('ts').set_index('ts'))
        df_gm = pd.read_sql("SELECT last_updated AS ts, btc_dominance, stablecoin_market_cap FROM global_metrics WHERE last_updated > NOW() - INTERVAL '7 days' ORDER BY last_updated ASC", conn)
        if not df_gm.empty:
            df_gm['ts'] = pd.to_datetime(df_gm['ts'], utc=True)
            frames.append(df_gm.set_index('ts'))
        df_st = pd.read_sql("SELECT timestamp AS ts, SUM(circulating) AS stablecoin_supply FROM stablecoins WHERE timestamp > NOW() - INTERVAL '7 days' GROUP BY timestamp ORDER BY timestamp ASC", conn)
        if not df_st.empty:
            df_st['ts'] = pd.to_datetime(df_st['ts'], utc=True)
            frames.append(df_st.set_index('ts'))
        df_fg = pd.read_sql("SELECT timestamp AS ts, value AS fear_greed FROM fear_greed WHERE timestamp > NOW() - INTERVAL '7 days' ORDER BY timestamp ASC", conn)
        if not df_fg.empty:
            df_fg['ts'] = pd.to_datetime(df_fg['ts'], utc=True)
            frames.append(df_fg.set_index('ts'))
        conn.close()
        return pd.concat(frames, axis=1, sort=True) if frames else pd.DataFrame()
    except Exception as e:
        log.error(f"Macro fetch failed: {e}")
        return pd.DataFrame()

def merge_macro_for_bot(df_btc, macro):
    if macro.empty: return df_btc
    df_btc = df_btc.sort_values('open_time').reset_index(drop=True)
    macro = macro.sort_index()
    result = df_btc.copy()
    def asof(left, cols, tol_h):
        cols = [c for c in cols if c in macro.columns]
        if not cols: return left
        right = macro[cols].dropna(how='all').reset_index().rename(columns={macro.index.name or 'index': 'ts'}).sort_values('ts')
        left_s = left.sort_values('open_time').rename(columns={'open_time':'ts'})
        m = pd.merge_asof(left_s, right, on='ts', direction='backward', allow_exact_matches=True, tolerance=pd.Timedelta(hours=tol_h))
        return m.rename(columns={'ts':'open_time'})
    result = asof(result, ['nasdaq','dxy'], 72)
    result = asof(result, ['btc_dominance','stablecoin_market_cap'], 6)
    result = asof(result, ['stablecoin_supply'], 6)
    result = asof(result, ['fear_greed'], 48)
    return result

def get_features_for_ml(df_1h, feature_cols):
    df = df_1h.copy()
    df['rsi_norm'] = df['rsi'] / 100.0
    df['rsi_7_norm'] = df['rsi_7'] / 100.0
    df['rsi_21_norm'] = df['rsi_21'] / 100.0
    df['macd_norm'] = df['macd_hist'] / df['close']
    df['ema50_diff'] = (df['close'] - df['ema_50']) / df['ema_50']
    df['ema20_diff'] = (df['close'] - df['ema_20']) / df['ema_20']
    df['ema200_diff'] = (df['close'] - df['ema_200']) / df['ema_200']
    df['vol_ratio'] = df['volume'] / df['vol_sma']
    df['atr7_pct'] = df['atr_7'] / df['close']
    df['atr14_pct'] = df['atr_14'] / df['close']
    df['atr50_pct'] = df['atr_50'] / df['close']
    df['atr100_pct'] = df['atr_100'] / df['close']
    df['avg_trade_norm'] = df['avg_trade_size'] / df['volume'].rolling(20).mean()
    df['pc1'] = df['price_change_1h']
    df['pc4'] = df['price_change_4h']
    df['pc24'] = df['price_change_24h']
    for lag in [1, 3, 6, 12, 24]:
        for f in ['rsi_norm','macd_norm','ema50_diff','vol_ratio','buy_ratio']:
            df[f'{f}_lag{lag}'] = df[f].shift(lag)
    df = df.dropna()
    if df.empty: return None
    macro = fetch_macro_for_bot()
    if not macro.empty:
        df_m = merge_macro_for_bot(df.reset_index(), macro).set_index('open_time')
        if 'nasdaq' in df_m.columns:
            df_m['nasdaq_pct_1h'] = df_m['nasdaq'].pct_change(1)
            df_m['nasdaq_pct_24h'] = df_m['nasdaq'].pct_change(24)
        if 'dxy' in df_m.columns:
            df_m['dxy_pct_1h'] = df_m['dxy'].pct_change(1)
            df_m['dxy_pct_24h'] = df_m['dxy'].pct_change(24)
        if 'fear_greed' in df_m.columns:
            df_m['fear_greed_norm'] = df_m['fear_greed'] / 100.0
        if 'btc_dominance' in df_m.columns:
            df_m['btc_dom_pct'] = df_m['btc_dominance'].pct_change(24)
        if 'stablecoin_supply' in df_m.columns:
            df_m['stable_flow_1h'] = df_m['stablecoin_supply'].pct_change(1)
            df_m['stable_flow_24h'] = df_m['stablecoin_supply'].pct_change(24)
        df = df_m
    cols = [c for c in feature_cols if c in df.columns]
    if not cols: return None
    return df[cols].iloc[[-1]].values

def load_ml_model(node):
    if not node or "params" not in node: return None, None
    p = node["params"]
    mt = p.get("model_type", "")
    if "model_file" in p and p["model_file"]:
        fpath = MODELS_DIR / p["model_file"]
        if not fpath.exists():
            log.error(f"Model file not found: {fpath}")
            return None, None
        try:
            if "xgb" in mt:
                bst = xgb.Booster(); bst.load_model(str(fpath)); return "xgb", bst
            elif "lgbm" in mt:
                import lightgbm as lgb; return "lgbm", lgb.Booster(model_file=str(fpath))
            elif "catboost" in mt:
                from catboost import CatBoostRegressor
                m = CatBoostRegressor(); m.load_model(str(fpath)); return "catboost", m
            else:
                with open(fpath, "rb") as f: return "sklearn", pickle.load(f)
        except Exception as e:
            log.error(f"Failed to load {fpath}: {e}")
            return None, None
    if "model_b64" in p:
        try:
            raw = base64.b64decode(p["model_b64"])
            if "xgb" in mt:
                bst = xgb.Booster(); bst.load_model(bytearray(raw)); return "xgb", bst
            elif "lgbm" in mt:
                import lightgbm as lgb; return "lgbm", lgb.Booster(model_str=raw.decode('utf-8'))
            elif "catboost" in mt:
                import tempfile, os as _os
                from catboost import CatBoostRegressor
                with tempfile.NamedTemporaryFile(suffix=".cbm", delete=False) as tmp:
                    tmp.write(raw); tmp_path = tmp.name
                m = CatBoostRegressor(); m.load_model(tmp_path); _os.remove(tmp_path); return "catboost", m
            else:
                return "sklearn", pickle.loads(raw)
        except Exception as e:
            log.error(f"Failed to load model_b64: {e}")
            return None, None
    return None, None

def predict_ml(node, df_1h):
    if not node or "params" not in node: return None
    feat = node["params"].get("features", [])
    mt, model = load_ml_model(node)
    if model is None: return None
    try:
        X = get_features_for_ml(df_1h, feat)
        if X is None or len(X) == 0: return None
        if mt == "xgb": return float(model.predict(xgb.DMatrix(X))[0])
        elif mt == "lgbm": return float(model.predict(X)[0])
        elif mt == "catboost": return float(model.predict(X)[0])
        else: return float(model.predict(X)[0])
    except Exception as e:
        log.error(f"ML predict failed: {e}")
        return None

def is_ml_ready(node, cfg):
    if not node: return False
    m = node.get("metrics", {}) or {}
    ml_cfg = cfg.get("ml_deploy", {})
    min_trades = int(ml_cfg.get("min_trades_for_live", 30))
    min_score = float(ml_cfg.get("min_score_for_live", 0.5))
    trades = int(m.get("trades", 0) or 0)
    score = float(m.get("score", -99999) or -99999)
    return trades >= min_trades and score >= min_score

def process_mode(mode, deals, bot_info, models, df_1h, config):
    cfg = config.get(mode, {})
    fee_pct = config.get("global", {}).get("fee_pct", 0.001)
    now_utc = datetime.now(timezone.utc)

    if mode in deals and "entry" in deals[mode]:
        deal = deals[mode]
        deal_type = deal.get("type", "LONG")
        entry = float(deal['entry']); sl = float(deal['sl']); tp = float(deal['tp'])
        deal_open_time = datetime.fromisoformat(deal["open_time"])
        df_to_check = df_1h[df_1h['open_time'] >= deal_open_time - timedelta(hours=1)]
        act_pct = cfg.get("trailing_activation_pct", 1.0)
        atr_mult = cfg.get("trailing_atr_multiplier", 2.0)
        exit_reason, exit_price = None, float(df_1h.iloc[-1]['close'])
        max_price, min_price = entry, entry
        log.info(f"🔄 [{mode.upper()}] {deal_type} | SL: {sl} | Price: {exit_price}")

        def check_candle(row):
            nonlocal sl, exit_reason, exit_price, max_price, min_price
            row_atr = float(row['atr_14']) if pd.notna(row['atr_14']) else 0.0
            if deal_type == "LONG":
                if row['low'] <= sl:
                    exit_reason = "TRAILING_STOP" if sl > entry else "STOP_LOSS"
                    exit_price = sl; return True
                if row['high'] >= tp:
                    exit_reason = "TAKE_PROFIT"; exit_price = tp; return True
                if row['high'] > max_price: max_price = row['high']
                if row_atr > 0 and max_price >= entry * (1 + act_pct/100):
                    atr_sl = max_price - (row_atr * atr_mult)
                    breakeven_sl = entry * (1 + (fee_pct * 2))
                    new_sl = max(atr_sl, breakeven_sl)
                    if new_sl > sl + 0.01:
                        sl = round(new_sl, 2)
                        sl_pct = round((sl - entry) / entry * 100, 2)
                        append_event({"type":"UPDATE","mode":mode,"deal_type":deal_type,"new_sl":sl,"sl_pct":sl_pct})
            else:
                if row['high'] >= sl:
                    exit_reason = "TRAILING_STOP" if sl < entry else "STOP_LOSS"
                    exit_price = sl; return True
                if row['low'] <= tp:
                    exit_reason = "TAKE_PROFIT"; exit_price = tp; return True
                if row['low'] < min_price: min_price = row['low']
                if row_atr > 0 and min_price <= entry * (1 - act_pct/100):
                    atr_sl = min_price + (row_atr * atr_mult)
                    breakeven_sl = entry * (1 - (fee_pct * 2))
                    new_sl = min(atr_sl, breakeven_sl)
                    if new_sl < sl - 0.01:
                        sl = round(new_sl, 2)
                        sl_pct = round((entry - sl) / entry * 100, 2)
                        append_event({"type":"UPDATE","mode":mode,"deal_type":deal_type,"new_sl":sl,"sl_pct":sl_pct})
            return False

        for _, row in df_to_check.iterrows():
            if check_candle(row): break

        close_h = cfg.get("close_hour_utc")
        if not exit_reason and mode == "intraday" and close_h is not None and now_utc.hour >= close_h:
            exit_reason, exit_price = "TIME_EXIT_DAY", float(df_1h.iloc[-1]['close'])

        if not exit_reason and mode == "swing":
            time_held = (now_utc - deal_open_time).total_seconds() / 3600.0
            time_exit_h = cfg.get("time_exit_h", 48)
            if time_held >= time_exit_h:
                exit_reason, exit_price = "TIME_EXIT", float(df_1h.iloc[-1]['close'])

        if exit_reason:
            pnl_pct = (exit_price - entry) / entry * 100 if deal_type == "LONG" else (entry - exit_price) / entry * 100
            pnl_pct -= (fee_pct * 2 * 100)
            pnl_usd = bot_info[mode]["balance"] * (pnl_pct / 100)
            bot_info[mode]["balance"] = round(bot_info[mode]["balance"] + pnl_usd, 2)
            append_event({"type":"CLOSE","mode":mode,"deal_type":deal_type,"reason":exit_reason,"pnl_pct":round(pnl_pct,2),"pnl_usd":round(pnl_usd,2),"new_balance":bot_info[mode]["balance"],"entry":entry,"exit":exit_price})
            closed = load_json(CLOSED_DEALS, [])
            closed.append({"type":deal_type,"entry":entry,"exit":exit_price,"pnl_pct":round(pnl_pct,2),"pnl_usd":round(pnl_usd,2),"reason":exit_reason,"mode":mode,"close_time":now_utc.isoformat(),"balance_after":bot_info[mode]["balance"],"active_signals":deal.get("active_signals",[])})
            save_json(CLOSED_DEALS, closed)
            del deals[mode]
            save_json(OPENED_DEALS, deals); save_json(BOT_INFO, bot_info)
        else:
            deal['sl'] = sl
            save_json(BOT_INFO, bot_info); save_json(OPENED_DEALS, deals)
        return

    node_long = models.get(mode, {}).get("long")
    node_short = models.get(mode, {}).get("short")
    ready_long = is_ml_ready(node_long, config)
    ready_short = is_ml_ready(node_short, config)

    price = float(df_1h.iloc[-1]['close'])
    rsi = float(df_1h.iloc[-1]['rsi'])
    macd_hist = float(df_1h.iloc[-1]['macd_hist'])
    ema = float(df_1h.iloc[-1]['ema_50'])
    vol_sma = float(df_1h.iloc[-1]['vol_sma'])
    cur_vol = float(df_1h.iloc[-1]['volume'])
    w = cfg.get("weights", {})

    score_long, active_long = 0.0, []
    pred_long = None
    if ready_long:
        pred_long = predict_ml(node_long, df_1h)
    if pred_long is not None:
        score_long = pred_long * 10; active_long = ["ml"]
        log.info(f"[{mode.upper()}] ML LONG pred={pred_long:.3f} -> score={score_long:.2f}")
    else:
        if not ready_long and node_long:
            m = node_long.get("metrics", {}) or {}
            log.info(f"[{mode.upper()}] LONG ML NOT READY (trades={m.get('trades',0)}<{config.get('ml_deploy',{}).get('min_trades_for_live',30)}, score={m.get('score',0)}) -> using WEIGHTS")
        if macd_hist > 0: score_long += w.get("macd",1.0); active_long.append("macd")
        if price > ema: score_long += w.get("ema",1.0); active_long.append("ema")
        if cur_vol > vol_sma: score_long += w.get("volume",1.0); active_long.append("volume")
        if rsi < 30: score_long += w.get("rsi_low",1.0); active_long.append("rsi_low")
        elif rsi < 70: score_long += w.get("rsi_mid",1.0); active_long.append("rsi_mid")

    score_short, active_short = 0.0, []
    pred_short = None
    if ready_short:
        pred_short = predict_ml(node_short, df_1h)
    if pred_short is not None:
        score_short = -pred_short * 10; active_short = ["ml"]
        log.info(f"[{mode.upper()}] ML SHORT pred={pred_short:.3f} -> score={score_short:.2f}")
    else:
        if not ready_short and node_short:
            m = node_short.get("metrics", {}) or {}
            log.info(f"[{mode.upper()}] SHORT ML NOT READY (trades={m.get('trades',0)}<{config.get('ml_deploy',{}).get('min_trades_for_live',30)}, score={m.get('score',0)}) -> using WEIGHTS")
        if macd_hist < 0: score_short += w.get("macd",1.0); active_short.append("macd")
        if price < ema: score_short += w.get("ema",1.0); active_short.append("ema")
        if cur_vol > vol_sma: score_short += w.get("volume",1.0); active_short.append("volume")
        if rsi > 70: score_short += w.get("rsi_high",1.0); active_short.append("rsi_high")
        elif rsi > 30: score_short += w.get("rsi_mid",1.0); active_short.append("rsi_mid")

    thresh = cfg.get("threshold", 2.0)
    can_open = True
    no_entry_str = cfg.get("no_entry_time_utc")
    if mode == "intraday" and no_entry_str:
        try:
            ne_h, ne_m = map(int, no_entry_str.split(":"))
            if now_utc.hour > ne_h or (now_utc.hour == ne_h and now_utc.minute >= ne_m):
                can_open = False
        except Exception: pass

    log.info(f"[{mode.upper()}] Price: {price:.1f} | LONG: {score_long:.2f} | SHORT: {score_short:.2f} | Thresh: {thresh:.2f}")

    if can_open and score_long >= thresh and score_long > score_short:
        tp = price * (1 + cfg.get("tp_pct", 2.0) / 100)
        sl = price * (1 - cfg.get("sl_pct", 1.0) / 100)
        deals[mode] = {"type":"LONG","entry":price,"tp":tp,"sl":sl,"open_time":now_utc.isoformat(),"active_signals":active_long}
        append_event({"type":"ENTRY","mode":mode,"deal_type":"LONG","entry":price,"tp":tp,"sl":sl})
        log.info(f"🚀 [{mode.upper()}] LONG SIGNAL!")
    elif can_open and score_short >= thresh and score_short > score_long:
        tp = price * (1 - cfg.get("tp_pct", 2.0) / 100)
        sl = price * (1 + cfg.get("sl_pct", 1.0) / 100)
        deals[mode] = {"type":"SHORT","entry":price,"tp":tp,"sl":sl,"open_time":now_utc.isoformat(),"active_signals":active_short}
        append_event({"type":"ENTRY","mode":mode,"deal_type":"SHORT","entry":price,"tp":tp,"sl":sl})
        log.info(f"🩸 [{mode.upper()}] SHORT SIGNAL!")

    bot_info[mode]["last_check_ts"] = now_utc.isoformat()
    save_json(BOT_INFO, bot_info)


def main():
    log.info(f"STARTING DUAL BOT v{VERSION}")
    config = load_json(CONFIG_FILE, {})
    if not config: return log.error("bot_config.json не найден")
    bot_info = ensure_bot_info(load_json(BOT_INFO, {}))
    models = load_json(MODEL_FILE)
    if not models.get("swing") or not models.get("intraday"):
        return log.error("Models not found")
    deals = load_json(OPENED_DEALS, {})
    if "intraday" not in deals: deals["intraday"] = {}
    if "swing" not in deals: deals["swing"] = {}

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT open_time, open, high, low, close, volume, taker_buy_volume, count FROM candles WHERE symbol=%s AND timeframe='15m' AND market_type='spot' ORDER BY open_time DESC LIMIT 500", (SYMBOL,))
            rows = list(reversed(cur.fetchall()))
        conn.close()
    except Exception as e:
        return log.error(f"DB error: {e}")

    df = pd.DataFrame([(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6]), int(r[7])) for r in rows], columns=['open_time','open','high','low','close','volume','taker_buy_volume','count'])
    df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
    
    # ---- ВСТРОЕННЫЙ DATA WATCHDOG ----
    if not check_data_freshness(df, config, bot_info):
        log.warning("Stale data detected. Bot paused. Exiting.")
        return

    df_1h = df.set_index('open_time').resample('1h').agg({
        'open':'first','high':'max','low':'min','close':'last',
        'volume':'sum','taker_buy_volume':'sum','count':'sum'
    }).dropna().reset_index()
    df_1h = calculate_indicators(df_1h.set_index('open_time')).reset_index()

    process_mode("intraday", deals, bot_info, models, df_1h, config)
    process_mode("swing", deals, bot_info, models, df_1h, config)

    save_json(OPENED_DEALS, deals); save_json(BOT_INFO, bot_info)


if __name__ == "__main__":
    main()