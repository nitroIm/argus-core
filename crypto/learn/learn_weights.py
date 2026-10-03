# ============================================================
# ARGUS-Trader - LEARN WEIGHTS
# ------------------------------------------------------------
# Updates explorer weights from closed trades.
# Only uses trades with explorer_breakdown (v9.5+ format).
# Writes crypto/mexc/simulator_01/state/weights.json
# ============================================================

import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.weights")

SIM_DIR = CRYPTO_ROOT / "mexc" / "simulator_01"
STATE_DIR = SIM_DIR / "state"
TRADES_FILE = STATE_DIR / "trades.json"
WEIGHTS_FILE = STATE_DIR / "weights.json"

LR = 0.2
MIN_SAMPLES = 5
MIN_WEIGHT = 0.3
MAX_WEIGHT = 2.0

SOURCES = [
    "ml", "news", "events", "causal",
    "levels", "patterns", "correlations",
    "db2_patterns", "db2_vectors", "anomaly",
]


def load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error("save %s: %s", path.name, e)


def collect_from_trade(trade):
    """Only new format. Returns list of (source, agreed)."""
    pnl = trade.get("pnl_usd")
    if pnl is None:
        return []
    direction = trade.get("direction", "LONG")
    won = pnl > 0

    br = trade.get("explorer_breakdown")
    if not isinstance(br, dict) or not br:
        return []

    out = []
    for src, info in br.items():
        if src not in SOURCES:
            continue
        try:
            contrib = float(info.get("contrib", 0))
        except Exception:
            continue
        if abs(contrib) < 1e-9:
            continue
        voted_dir = "LONG" if contrib > 0 else "SHORT"
        agreed = (voted_dir == direction)
        out.append((src, agreed, won))
    return out


def main():
    log.info("=" * 60)
    log.info("ARGUS LEARN WEIGHTS")
    log.info("=" * 60)

    trades = load_json(TRADES_FILE, [])
    if not trades:
        log.warning("no trades, nothing to learn")
        return

    hits = {s: 0 for s in SOURCES}
    miss = {s: 0 for s in SOURCES}
    counted = 0

    for t in trades:
        if not isinstance(t, dict):
            continue
        pairs = collect_from_trade(t)
        if not pairs:
            continue
        for src, agreed, won in pairs:
            if agreed == won:
                hits[src] += 1
            else:
                miss[src] += 1
        counted += 1

    log.info(
        "trades with breakdown: %d / %d",
        counted, len(trades),
    )

    if counted == 0:
        log.info("no new-format trades yet, skip")
        return

    weights = load_json(WEIGHTS_FILE, {})
    if not weights:
        log.warning("weights.json empty")
        return

    changed = []
    for src in SOURCES:
        n = hits[src] + miss[src]
        if n < MIN_SAMPLES:
            continue
        hit_rate = hits[src] / n
        w_old = float(weights.get(src, 1.0))
        w_new = w_old * (1 + LR * (hit_rate - 0.5))
        w_new = max(MIN_WEIGHT, min(MAX_WEIGHT, w_new))
        w_new = round(w_new, 4)
        if abs(w_new - w_old) > 1e-6:
            weights[src] = w_new
            changed.append((src, w_old, w_new, n, hit_rate))

    if not changed:
        log.info("no updates (threshold not met)")
        return

    for src, wo, wn, n, hr in changed:
        log.info(
            "%s: %.4f -> %.4f (n=%d hit=%.2f)",
            src, wo, wn, n, hr,
        )

    weights["updated_at"] = datetime.now(
        timezone.utc
    ).isoformat()
    save_json(WEIGHTS_FILE, weights)
    log.info("weights saved: %s", WEIGHTS_FILE.name)
    log.info("=" * 60)


if __name__ == "__main__":
    main()