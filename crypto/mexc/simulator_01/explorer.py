# ============================================================
# ARGUS - EXPLORER (simulator)
# ------------------------------------------------------------
# Читает все источники сигналов. Считает общий score.
# Веса - в state/weights.json (обучаются из сделок).
# ============================================================

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(CRYPTO_ROOT))

DB2_URL = (os.getenv("ARGUS_DB_URL_2") or "").strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("explorer")

DATA_DIR = CRYPTO_ROOT / "data"
LEARN_DIR = CRYPTO_ROOT / "learn"
STATE_DIR = SCRIPT_DIR / "state"

WEIGHTS_FILE = STATE_DIR / "weights.json"
LEVELS_FILE = DATA_DIR / "levels_analysis.json"
PATTERNS_FILE = DATA_DIR / "patterns_analysis.json"
CORREL_FILE = DATA_DIR / "correlations.json"
EVENTS_FILE = DATA_DIR / "events_analysis.json"
CAUSAL_FILE = DATA_DIR / "causal_analysis.json"
NEWS_FILE = DATA_DIR / "news_sentiment.json"
SIGNALS_FILE = LEARN_DIR / "last_signals.json"

DEFAULT_WEIGHTS = {
    "ml": 1.0,
    "news": 0.4,
    "events": 0.6,
    "causal": 0.5,
    "levels": 1.0,
    "patterns": 1.0,
    "correlations": 1.0,
    "db2_patterns": 1.2,
    "db2_vectors": 0.8,
    "threshold": 0.30,
}

_db2_conn = None


def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def load_weights():
    if not WEIGHTS_FILE.exists():
        save_weights(DEFAULT_WEIGHTS)
        return dict(DEFAULT_WEIGHTS)
    w = load_json(WEIGHTS_FILE, {})
    for k, v in DEFAULT_WEIGHTS.items():
        if k not in w:
            w[k] = v
    return w


def save_weights(w):
    try:
        with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
            json.dump(w, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("save weights: %s", e)


def _get_db2():
    global _db2_conn
    if _db2_conn is None or _db2_conn.closed:
        if not DB2_URL:
            return None
        try:
            import psycopg
            _db2_conn = psycopg.connect(DB2_URL, connect_timeout=15)
        except Exception as e:
            log.warning("DB2 connect: %s", e)
            return None
    return _db2_conn


def signal_ml(symbol):
    data = load_json(SIGNALS_FILE, {})
    for s in data.get("signals", []):
        if s.get("symbol") != symbol:
            continue
        action = s.get("action", "WAIT")
        conf = float(s.get("confidence", 0) or 0)
        prob_up = float(s.get("prob_up", 0.5) or 0.5)
        raw = (prob_up - 0.5) * 2.0 * conf
        return {"raw": round(raw, 4), "action": action,
                "conf": conf, "prob_up": prob_up}
    return {"raw": 0.0, "action": "WAIT", "conf": 0, "prob_up": 0.5}


def signal_news(symbol):
    data = load_json(NEWS_FILE, {})
    sc = None
    if isinstance(data, dict):
        if "score" in data:
            sc = data.get("score")
        elif "symbols" in data:
            sym_data = data["symbols"].get(symbol, {})
            sc = sym_data.get("score")
    if sc is None:
        return {"raw": 0.0, "found": False}
    try:
        sc = float(sc)
    except Exception:
        return {"raw": 0.0, "found": False}
    return {"raw": round(sc, 4), "found": True}


EVENT_SIGN = {
    "rsi_overbought": -1.0,
    "rsi_oversold": 1.0,
    "funding_spike_pos": -1.0,
    "funding_spike_neg": 1.0,
    "oi_spike": 0.0,
    "ls_long_extreme": -1.0,
    "ls_short_extreme": 1.0,
    "volume_spike": 0.0,
    "rise_1h": 0.5,
    "rise_4h": 0.5,
    "fall_1h": -0.5,
    "fall_4h": -0.5,
}


def signal_events(symbol):
    data = load_json(EVENTS_FILE, {})
    sym = data.get("symbols", {}).get(symbol, {})
    events = sym.get("events", [])
    if not events:
        return {"raw": 0.0, "active": []}
    now = datetime.now(timezone.utc)
    total = 0.0
    active = []
    for e in events[-50:]:
        ts = e.get("timestamp")
        etype = e.get("event_type", "")
        if not ts or etype not in EVENT_SIGN:
            continue
        try:
            if isinstance(ts, str):
                edt = datetime.fromisoformat(ts)
                if edt.tzinfo is None:
                    edt = edt.replace(tzinfo=timezone.utc)
            else:
                continue
        except Exception:
            continue
        age_h = (now - edt).total_seconds() / 3600
        if age_h > 4:
            continue
        w = EVENT_SIGN[etype]
        total += w
        active.append({"type": etype, "age_h": round(age_h, 1), "w": w})
    if not active:
        return {"raw": 0.0, "active": []}
    raw = max(-1.0, min(1.0, total / max(1, len(active))))
    return {"raw": round(raw, 4), "active": active[:5]}


def signal_causal(symbol):
    data = load_json(CAUSAL_FILE, {})
    sym = data.get("symbols", {}).get(symbol, {})
    summary = sym.get("summary_by_type", {})
    if not summary:
        return {"raw": 0.0, "found": False}
    total = 0.0
    count = 0
    for etype, s in summary.items():
        ch = s.get("avg_change_24h")
        if ch is None:
            continue
        n = s.get("count", 0)
        total += float(ch) * min(n, 10)
        count += min(n, 10)
    if count == 0:
        return {"raw": 0.0, "found": False}
    avg = total / count
    raw = max(-1.0, min(1.0, avg / 2.0))
    return {"raw": round(raw, 4), "avg_change": round(avg, 4)}


def signal_levels(symbol):
    data = load_json(LEVELS_FILE, {})
    sym = data.get("symbols", {}).get(symbol, {})
    if not sym:
        return {"raw": 0.0, "found": False}
    price = sym.get("current_price")
    if not price:
        return {"raw": 0.0, "found": False}

    sup = sym.get("supports", [])
    res = sym.get("resistances", [])
    d_sup = sup[0].get("distance_pct") if sup else None
    d_res = res[0].get("distance_pct") if res else None

    raw = 0.0
    if d_sup is not None and d_sup < 1.5:
        raw += 0.5 * (1 - d_sup / 1.5)
    if d_res is not None and d_res < 1.5:
        raw -= 0.5 * (1 - d_res / 1.5)

    pos = None
    extremes = sym.get("extremes", {})
    e30 = extremes.get("30d", {})
    h30 = e30.get("high")
    l30 = e30.get("low")
    if h30 and l30 and h30 > l30:
        pos = (price - l30) / (h30 - l30)
        raw += (0.5 - pos) * 0.8

    raw = max(-1.0, min(1.0, raw))
    return {"raw": round(raw, 4), "d_sup": d_sup,
            "d_res": d_res,
            "pos_30d": round(pos, 3) if pos is not None else None}


def signal_patterns(symbol):
    data = load_json(PATTERNS_FILE, {})
    sym = data.get("symbols", {}).get(symbol, {})
    if not sym:
        return {"raw": 0.0, "found": False}

    mk = sym.get("markov", {})
    p10 = float(mk.get("p_1_given_0", 0) or 0)
    p00 = float(mk.get("p_0_given_0", 0) or 0)
    p11 = float(mk.get("p_1_given_1", 0) or 0)
    p01 = float(mk.get("p_0_given_1", 0) or 0)

    binary = sym.get("binary_string", "")
    last_bit = binary[-1] if binary else "0"

    if last_bit == "1":
        raw = (p11 - p01) * 2
    else:
        raw = (p10 - p00) * 2

    match = None
    ngrams = sym.get("ngrams_top", [])
    if binary and len(binary) >= 4:
        current = binary[-4:]
        for ng in ngrams:
            if ng.get("ngram") == current:
                p_up = float(ng.get("p_up", 0.5))
                n = ng.get("count", 0)
                w = min(1.0, n / 30.0)
                ngram_raw = (p_up - 0.5) * 2 * w
                raw = (raw + ngram_raw) / 2
                match = {"ngram": current, "p_up": p_up, "n": n}
                break

    raw = max(-1.0, min(1.0, raw))
    return {"raw": round(raw, 4), "p10": p10, "p00": p00,
            "p11": p11, "p01": p01,
            "last_bit": last_bit, "ngram_match": match}


def signal_correlations(symbol):
    data = load_json(CORREL_FILE, {})
    sym = data.get("symbols", {}).get(symbol, {})
    rules = sym.get("rules", [])
    if not rules:
        return {"raw": 0.0, "found": False}

    total = 0.0
    count = 0
    active = []
    for r in rules:
        direction = r.get("direction")
        conf = float(r.get("confidence", 0) or 0)
        n = int(r.get("samples", 0) or 0)
        if n < 5 or conf < 0.55:
            continue
        sign = 1.0 if direction == "up" else (-1.0 if direction == "down" else 0)
        if sign == 0:
            continue
        w = (conf - 0.5) * 2 * min(1.0, n / 30.0)
        total += sign * w
        count += 1
        active.append({"rule": r.get("rule"), "dir": direction,
                       "conf": conf, "n": n})

    if count == 0:
        return {"raw": 0.0, "active": []}
    raw = max(-1.0, min(1.0, total / count))
    return {"raw": round(raw, 4), "active": active[:5]}


def signal_db2_patterns(symbol):
    conn = _get_db2()
    if conn is None:
        return {"raw": 0.0, "found": False}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (symbol) symbol, change_pct "
                "FROM asia_market ORDER BY symbol, timestamp DESC"
            )
            market = {}
            for s, ch in cur.fetchall():
                if ch is not None:
                    market[s] = float(ch)
            cur.execute(
                "SELECT source_symbol, condition_pct, direction, "
                "lag_hours, samples, hit_rate FROM asia_patterns "
                "WHERE target_symbol = %s AND samples >= 5",
                (symbol,),
            )
            total = 0.0
            count = 0
            active = []
            for src, cond, direction, lag, n, hit in cur.fetchall():
                chg = market.get(src)
                if chg is None:
                    continue
                cond = float(cond)
                if direction == "up" and chg <= cond:
                    continue
                if direction == "down" and chg >= -cond:
                    continue
                sign = 1.0 if direction == "up" else -1.0
                w = float(hit) if hit else 0.0
                total += sign * w
                count += 1
                active.append({
                    "src": src, "dir": direction,
                    "cond": cond, "lag": lag,
                    "hit": float(hit) if hit else 0, "n": n,
                })
            if count == 0:
                return {"raw": 0.0, "active": []}
            raw = max(-1.0, min(1.0, total / count))
            return {"raw": round(raw, 4), "active": active[:5]}
    except Exception as e:
        log.warning("db2 patterns: %s", e)
        return {"raw": 0.0, "found": False}


def signal_db2_vectors(symbol):
    conn = _get_db2()
    if conn is None:
        return {"raw": 0.0, "found": False}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_symbol, lag_hours, corr, samples "
                "FROM impact_vectors WHERE target_symbol = %s "
                "AND samples >= 20 ORDER BY ABS(corr) DESC LIMIT 10",
                (symbol,),
            )
            total = 0.0
            count = 0
            for src, lag, corr, n in cur.fetchall():
                if corr is None:
                    continue
                c = float(corr)
                w = min(1.0, float(n) / 100.0)
                total += c * w
                count += 1
            if count == 0:
                return {"raw": 0.0, "found": False}
            raw = max(-1.0, min(1.0, total / count))
            return {"raw": round(raw, 4)}
    except Exception as e:
        log.warning("db2 vectors: %s", e)
        return {"raw": 0.0, "found": False}


def signal_db2_market():
    conn = _get_db2()
    if conn is None:
        return {"markets": {}}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (symbol) symbol, change_pct "
                "FROM asia_market ORDER BY symbol, timestamp DESC"
            )
            market = {}
            for s, ch in cur.fetchall():
                if ch is not None:
                    market[s] = float(ch)
            return {"markets": market}
    except Exception:
        return {"markets": {}}


SOURCES = [
    ("ml", signal_ml),
    ("news", signal_news),
    ("events", signal_events),
    ("causal", signal_causal),
    ("levels", signal_levels),
    ("patterns", signal_patterns),
    ("correlations", signal_correlations),
    ("db2_patterns", signal_db2_patterns),
    ("db2_vectors", signal_db2_vectors),
]


def analyze(symbol):
    weights = load_weights()
    total = 0.0
    breakdown = {}
    active_all = []

    for name, fn in SOURCES:
        try:
            res = fn(symbol)
        except Exception as e:
            log.warning("%s: %s", name, e)
            res = {"raw": 0.0}
        raw = float(res.get("raw", 0) or 0)
        w = float(weights.get(name, 0) or 0)
        contrib = raw * w
        total += contrib
        breakdown[name] = {
            "raw": round(raw, 4),
            "weight": w,
            "contrib": round(contrib, 4),
        }
        if res.get("active"):
            for a in res["active"]:
                active_all.append({"src": name, **a})

    market_info = signal_db2_market()
    threshold = float(weights.get("threshold", 0.3))

    if total >= threshold:
        direction = "LONG"
    elif total <= -threshold:
        direction = "SHORT"
    else:
        direction = "NONE"

    return {
        "symbol": symbol,
        "score": round(total, 4),
        "direction": direction,
        "threshold": threshold,
        "breakdown": breakdown,
        "active": active_all[:15],
        "markets": market_info.get("markets", {}),
    }


def close_db2():
    global _db2_conn
    if _db2_conn is not None and not _db2_conn.closed:
        _db2_conn.close()
    _db2_conn = None


if __name__ == "__main__":
    for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]:
        r = analyze(s)
        print("")
        print("=" * 50)
        print(r["symbol"], r["direction"],
              "score=", r["score"],
              "thr=", r["threshold"])
        for k, v in r["breakdown"].items():
            print("  ", k, "raw=", v["raw"],
                  "w=", v["weight"],
                  "->", v["contrib"])
        if r["active"]:
            print("  active:")
            for a in r["active"][:5]:
                print("    ", a)
    close_db2()