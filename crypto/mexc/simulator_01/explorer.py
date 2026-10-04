# ============================================================
# ARGUS - EXPLORER (simulator)
# ------------------------------------------------------------
# v6: smart signal_news (uses all fields, file age,
#     trust, cross-confirm, balance, volume).
# v5: read regime from patterns_analysis.json.
# v4: signal_ml respects action=WAIT.
# ============================================================

import os
import sys
import json
import time
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
    "news": 0.6,
    "events": 0.8,
    "causal": 0.7,
    "levels": 1.0,
    "patterns": 1.0,
    "correlations": 1.0,
    "db2_patterns": 1.2,
    "db2_vectors": 0.8,
    "anomaly": 1.0,
    "regime": 1.0,
    "threshold": 0.30,
}

_db2_conn = None
_db1_conn = None


def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def file_age_hours(path):
    if not path.exists():
        return None
    return (time.time() - path.stat().st_mtime) / 3600


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


def _get_db1():
    global _db1_conn
    if _db1_conn is None or _db1_conn.closed:
        try:
            import psycopg
            from config import DB_URL
            if not DB_URL:
                return None
            _db1_conn = psycopg.connect(DB_URL, connect_timeout=15)
        except Exception as e:
            log.warning("DB1 connect: %s", e)
            return None
    return _db1_conn


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


def signal_regime(symbol):
    """Read regime from patterns_analysis.json."""
    data = load_json(PATTERNS_FILE, {})
    sym = data.get("symbols", {}).get(symbol, {})
    regime = sym.get("regime", {})
    if not regime:
        return {
            "raw": 0.0,
            "label": "unknown",
            "trade_allowed": True,
            "preferred_direction": "both",
        }

    label = regime.get("label", "unknown")
    allowed = regime.get("trade_allowed", True)
    pref = regime.get("preferred_direction", "both")

    if not allowed:
        raw = 0.0
    elif pref == "LONG":
        raw = 0.3
    elif pref == "SHORT":
        raw = -0.3
    else:
        raw = 0.0

    return {
        "raw": round(raw, 4),
        "label": label,
        "trade_allowed": allowed,
        "preferred_direction": pref,
        "reason": regime.get("reason", ""),
        "up_ratio": regime.get("up_ratio"),
        "vol_ratio": regime.get("vol_ratio"),
    }


def signal_ml(symbol):
    data = load_json(SIGNALS_FILE, {})
    for s in data.get("signals", []):
        if s.get("symbol") != symbol:
            continue
        action = s.get("action", "WAIT")
        conf = float(s.get("confidence", 0) or 0)
        prob_up = float(s.get("prob_up", 0.5) or 0.5)

        if action == "WAIT":
            return {"raw": 0.0, "action": action,
                    "conf": conf, "prob_up": prob_up}

        raw = (prob_up - 0.5) * 2.0 * conf
        return {"raw": round(raw, 4), "action": action,
                "conf": conf, "prob_up": prob_up}
    return {"raw": 0.0, "action": "WAIT", "conf": 0, "prob_up": 0.5}


def signal_news(symbol):
    """Smart news signal v6.

    Uses: avg_sentiment, bullish/bearish/neutral counts,
    fake_count, cross_confirmed, total_news, file age.
    News is market-wide (not per-symbol), so all pairs
    get same raw — but only when file is fresh.
    """
    data = load_json(NEWS_FILE, {})
    if not isinstance(data, dict) or not data:
        return {"raw": 0.0, "found": False}

    # --- age check ---
    age_h = file_age_hours(NEWS_FILE)
    if age_h is None:
        return {"raw": 0.0, "found": False, "src": "no_file"}
    if age_h > 24:
        return {"raw": 0.0, "found": False,
                "src": "stale", "age_h": round(age_h, 1)}

    # --- counters ---
    total = int(data.get("total_news", 0) or 0)
    if total < 5:
        return {"raw": 0.0, "found": False, "src": "few_news"}

    bull = int(data.get("bullish_count", 0) or 0)
    bear = int(data.get("bearish_count", 0) or 0)
    neu = int(data.get("neutral_count", 0) or 0)
    fake = int(data.get("fake_count", 0) or 0)
    cross = int(data.get("cross_confirmed", 0) or 0)

    try:
        avg = float(data.get("avg_sentiment", 0) or 0)
    except Exception:
        avg = 0.0
    avg = max(-1.0, min(1.0, avg))

    # --- 1. balance: (bull - bear) / all ---
    denom = bull + bear + neu
    if denom > 0:
        balance = (bull - bear) / denom
    else:
        balance = 0.0

    # --- 2. trust: fake ratio discount ---
    fake_ratio = fake / max(1, total)
    trust = 1.0 - min(0.7, fake_ratio * 2.0)

    # --- 3. cross-confirm ---
    # many cross-confirmed = stronger
    confirm = min(1.0, cross / max(3, total * 0.3))

    # --- 4. volume: fewer than 30 news = weak ---
    volume = min(1.0, total / 30.0)

    # --- 5. freshness: news lose weight over hours ---
    fresh = max(0.3, 1.0 - age_h / 24.0)

    # --- combine ---
    avg_part = avg * 2.0
    combined = 0.4 * avg_part + 0.6 * balance
    raw = combined * trust * (0.5 + 0.5 * confirm)
    raw = raw * volume * fresh
    raw = max(-1.0, min(1.0, raw))

    return {
        "raw": round(raw, 4),
        "found": True,
        "avg_sent": round(avg, 3),
        "balance": round(balance, 3),
        "trust": round(trust, 3),
        "confirm": round(confirm, 3),
        "volume": round(volume, 3),
        "fresh": round(fresh, 3),
        "mood": data.get("mood", ""),
        "bull": bull,
        "bear": bear,
        "neu": neu,
        "fake": fake,
        "total": total,
        "cross": cross,
        "age_h": round(age_h, 1),
    }


EVENT_SIGN = {
    "rsi_overbought": -1.0,
    "rsi_oversold": 1.0,
    "funding_spike_pos": -1.0,
    "funding_spike_neg": 1.0,
    "oi_spike": 0.0,
    "ls_long_extreme": -0.6,
    "ls_short_extreme": 0.6,
    "volume_spike": 0.0,
    "rise_1h": 0.7,
    "rise_4h": 0.7,
    "fall_1h": -0.7,
    "fall_4h": -0.7,
    "new_high_7d": 0.4,
}


def signal_events(symbol):
    data = load_json(EVENTS_FILE, {})
    sym = data.get("symbols", {}).get(symbol, {})
    events = sym.get("recent_events", [])
    if not events:
        return {"raw": 0.0, "active": []}
    now = datetime.now(timezone.utc)
    total = 0.0
    weight_sum = 0.0
    active = []
    for e in events:
        ts = e.get("timestamp")
        etype = e.get("type", "")
        if not ts or etype not in EVENT_SIGN:
            continue
        try:
            s = ts.replace(" ", "T") if isinstance(ts, str) else ts
            edt = datetime.fromisoformat(s)
            if edt.tzinfo is None:
                edt = edt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        age_h = (now - edt).total_seconds() / 3600
        if age_h > 8:
            continue
        freshness = max(0.2, 1.0 - age_h / 8.0)
        w = EVENT_SIGN[etype] * freshness
        total += w
        weight_sum += freshness
        active.append({
            "type": etype,
            "age_h": round(age_h, 1),
            "w": round(w, 3),
        })
    if weight_sum == 0:
        return {"raw": 0.0, "active": []}
    raw = max(-1.0, min(1.0, total / weight_sum))
    return {"raw": round(raw, 4), "active": active[:5]}


def signal_causal(symbol):
    conn = _get_db1()
    if conn is None:
        return {"raw": 0.0, "found": False, "src": "no_db1"}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT e.event_type, "
                "AVG(c.funding_rate), "
                "AVG(c.oi_change_pct), "
                "AVG(c.ls_ratio), "
                "COUNT(*) "
                "FROM events e "
                "JOIN causal_links c ON c.event_id = e.id "
                "WHERE e.symbol = %s "
                "AND e.event_type IN "
                "('rise_1h','rise_4h','fall_1h','fall_4h') "
                "GROUP BY e.event_type",
                (symbol,),
            )
            groups = {}
            for et, fnd, oi, ls, n in cur.fetchall():
                groups[et] = {
                    "funding": float(fnd) if fnd is not None else None,
                    "oi": float(oi) if oi is not None else None,
                    "ls": float(ls) if ls is not None else None,
                    "n": int(n or 0),
                }

            cur.execute(
                "SELECT funding_rate, oi_change_pct, ls_ratio "
                "FROM features_hourly WHERE symbol = %s "
                "ORDER BY timestamp DESC LIMIT 1",
                (symbol,),
            )
            row = cur.fetchone()
            if not row:
                return {"raw": 0.0, "found": False, "src": "no_features"}
            f_now, oi_now, ls_now = row
    except Exception as e:
        log.warning("db1 causal: %s", e)
        return {"raw": 0.0, "found": False, "src": "err"}

    total = 0.0
    count = 0
    matched = []
    for et, g in groups.items():
        if g["n"] < 2:
            continue
        sign = 1.0 if et.startswith("rise") else -1.0

        score = 0.0
        checks = 0

        if g["ls"] is not None and ls_now is not None:
            ls_now_f = float(ls_now)
            diff = abs(ls_now_f - g["ls"])
            score += max(0, 1.0 - diff / 0.3)
            checks += 1

        if g["funding"] is not None and f_now is not None:
            f_now_f = float(f_now)
            diff = abs(f_now_f - g["funding"])
            score += max(0, 1.0 - diff / 0.0005)
            checks += 1

        if checks == 0:
            continue
        sim = score / checks
        if sim < 0.25:
            continue

        w = sign * sim * min(1.0, g["n"] / 20.0)
        total += w
        count += 1
        matched.append({
            "type": et,
            "sim": round(sim, 2),
            "n": g["n"],
            "w": round(w, 3),
        })

    if count == 0:
        return {"raw": 0.0, "found": False, "src": "db1_empty"}
    raw = max(-1.0, min(1.0, total / count))
    return {
        "raw": round(raw, 4),
        "src": "db1",
        "active": matched,
    }


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
        edge = float(r.get("edge", 0) or 0)
        if n < 10 or conf < 0.55:
            continue
        sign = 1.0 if direction == "up" else (
            -1.0 if direction == "down" else 0
        )
        if sign == 0:
            continue
        w = min(1.0, edge / 0.3) * min(1.0, n / 30.0)
        total += sign * w
        count += 1
        active.append({
            "rule": r.get("rule"),
            "dir": direction,
            "conf": conf,
            "n": n,
            "edge": edge,
        })

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
                "WHERE target_symbol = %s AND samples >= 10",
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


ANOMALY_SIGN = {
    "stop_hunting_upper": -0.7,
    "stop_hunting_lower": 0.7,
    "pump_dump": -0.6,
    "wash_trading": 0.0,
    "cross_exchange": 0.0,
}


def signal_anomaly(symbol):
    conn = _get_db1()
    if conn is None:
        return {"raw": 0.0, "found": False, "src": "no_db1"}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT anomaly_type, severity, details, created_at "
                "FROM anomaly_log WHERE symbol = %s "
                "ORDER BY created_at DESC LIMIT 10",
                (symbol,),
            )
            rows = cur.fetchall()
    except Exception as e:
        log.warning("anomaly db1: %s", e)
        return {"raw": 0.0, "found": False, "src": "err"}

    now = datetime.now(timezone.utc)
    total = 0.0
    count = 0
    active = []
    for atype, sev, details, cts in rows:
        if cts is None:
            continue
        if cts.tzinfo is None:
            cts = cts.replace(tzinfo=timezone.utc)
        age_h = (now - cts).total_seconds() / 3600
        if age_h > 6:
            continue

        key = atype
        if atype == "stop_hunting" and isinstance(details, dict):
            wt = details.get("wick_type", "")
            if wt == "upper":
                key = "stop_hunting_upper"
            elif wt == "lower":
                key = "stop_hunting_lower"

        if key not in ANOMALY_SIGN:
            continue

        base = ANOMALY_SIGN[key]
        if base == 0.0:
            continue

        freshness = max(0.2, 1.0 - age_h / 6.0)
        w = base * freshness
        total += w
        count += 1
        active.append({
            "type": atype,
            "key": key,
            "age_h": round(age_h, 1),
            "sev": sev,
            "w": round(w, 3),
        })

    if count == 0:
        return {"raw": 0.0, "found": False, "src": "empty"}
    raw = max(-1.0, min(1.0, total / count))
    return {"raw": round(raw, 4), "src": "db1", "active": active[:5]}


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
    ("regime", signal_regime),
    ("ml", signal_ml),
    ("news", signal_news),
    ("events", signal_events),
    ("causal", signal_causal),
    ("levels", signal_levels),
    ("patterns", signal_patterns),
    ("correlations", signal_correlations),
    ("db2_patterns", signal_db2_patterns),
    ("db2_vectors", signal_db2_vectors),
    ("anomaly", signal_anomaly),
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

    regime_info = signal_regime(symbol)
    market_info = signal_db2_market()
    threshold = float(weights.get("threshold", 0.3))

    trade_allowed = regime_info.get("trade_allowed", True)
    preferred = regime_info.get("preferred_direction", "both")

    if not trade_allowed:
        direction = "NONE"
        log.info(
            "  [%s] regime=%s -> trade_allowed=False, NONE",
            symbol, regime_info.get("label", "?"),
        )
    elif total >= threshold:
        direction = "LONG"
    elif total <= -threshold:
        direction = "SHORT"
    else:
        direction = "NONE"

    if direction == "LONG" and preferred == "SHORT":
        log.info(
            "  [%s] regime=%s forbids LONG, NONE",
            symbol, regime_info.get("label", "?"),
        )
        direction = "NONE"
    elif direction == "SHORT" and preferred == "LONG":
        log.info(
            "  [%s] regime=%s forbids SHORT, NONE",
            symbol, regime_info.get("label", "?"),
        )
        direction = "NONE"

    return {
        "symbol": symbol,
        "score": round(total, 4),
        "direction": direction,
        "threshold": threshold,
        "breakdown": breakdown,
        "active": active_all[:15],
        "markets": market_info.get("markets", {}),
        "regime": regime_info,
    }


def close_all():
    global _db1_conn, _db2_conn
    if _db1_conn is not None and not _db1_conn.closed:
        _db1_conn.close()
    if _db2_conn is not None and not _db2_conn.closed:
        _db2_conn.close()
    _db1_conn = None
    _db2_conn = None


if __name__ == "__main__":
    for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]:
        r = analyze(s)
        print("")
        print("=" * 50)
        print(r["symbol"], r["direction"],
              "score=", r["score"],
              "thr=", r["threshold"])
        print("  regime:", r["regime"].get("label"),
              "allowed:", r["regime"].get("trade_allowed"),
              "pref:", r["regime"].get("preferred_direction"))
        for k, v in r["breakdown"].items():
            print("  ", k, "raw=", v["raw"],
                  "w=", v["weight"],
                  "->", v["contrib"])
        if r["active"]:
            print("  active:")
            for a in r["active"][:5]:
                print("    ", a)
    close_all()