# ============================================================
# ARGUS - SIGNAL BOT v9.4.6
# Path: crypto/signals/signal_bot.py
#
# ИЗМЕНЕНИЯ v9.4.6 (относительно v9.4.5):
#   - ФИКС: UPDATE-события теперь содержат sl_pct (был KeyError в report)
#   - LONG:  sl_pct = (sl - entry) / entry * 100
#   - SHORT: sl_pct = (entry - sl) / entry * 100
# ============================================================

import os, json, logging, base64, pickle
from pathlib import Path
from datetime import datetime, timezone, timedelta
import psycopg
import pandas as pd
import numpy as np
import xgboost as xgb

VERSION = "9.4.6"
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
        deal_type = deal.get("type", "L