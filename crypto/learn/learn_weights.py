# ============================================================
# ARGUS-Trader - LEARN WEIGHTS v3
# ------------------------------------------------------------
# v3: MIN_SAMPLES 5 -> 20 (5 was pure noise for
#     regime-aware updates).
#     + log skip reasons (no trades / no breakdown /
#       few samples).
#     + don't drop regime_weights section on empty
#       update (preserve last known values).
#     + track matched trades count per source.
# v2: per-regime weights (trend_up/down/flat/chop/...).
# v1: base weights from hits/misses.
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
MIN_SAMPLES = 20
MIN_WEIGHT = 0.3
MAX_WEIGHT = 2.0

SOURCES = [
    "ml", "news", "events", "causal",
    "levels", "patterns", "correlations",
    "db2_patterns", "db2_vectors", "anomaly",
    "regime",
]

REGIMES = [
    "trend_up", "trend_down", "flat",
    "chop", "volatile", "unknown",
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
        with open(
            path, "w", encoding="utf-8"
        ) as f:
            json.dump(
                data, f,
                ensure_ascii=False, indent=2,
            )
    except Exception as e:
        log.error("save %s: %s", path.name, e)


def collect_from_trade(trade):
    """Return list of (source, agreed, won, regime)."""
    pnl = trade.get("pnl_usd")
    if pnl is None:
        return []
    direction = trade.get("direction", "LONG")
    won = pnl > 0
    regime = trade.get("regime_label") or "unknown"

    br = trade.get("explorer_breakdown")
    if not isinstance(br, dict) or not br:
        return []

    out = []
    for src, info in br.items():
        if src not in SOURCES:
            continue
        if not isinstance(info, dict):
            continue
        try:
            contrib = float(info.get("contrib", 0))
        except Exception:
            continue
        if abs(contrib) < 1e-9:
            continue
        voted_dir = (
            "LONG" if contrib > 0 else "SHORT"
        )
        agreed = (voted_dir == direction)
        out.append((src, agreed, won, regime))
    return out


def compute_update(w_old, hits, total):
    if total < MIN_SAMPLES:
        return None
    hit_rate = hits / total
    w_new = w_old * (1 + LR * (hit_rate - 0.5))
    w_new = max(MIN_WEIGHT, min(MAX_WEIGHT, w_new))
    return round(w_new, 4), round(hit_rate, 4)


def main():
    log.info("=" * 60)
    log.info(
        "ARGUS LEARN WEIGHTS v3 "
        "(regime-aware, min=%d)", MIN_SAMPLES,
    )
    log.info("=" * 60)

    trades = load_json(TRADES_FILE, [])
    if not trades:
        log.warning("no trades, nothing to learn")
        return

    log.info("trades loaded: %d", len(trades))

    base_hits = {s: 0 for s in SOURCES}
    base_total = {s: 0 for s in SOURCES}

    regime_hits = {
        r: {s: 0 for s in SOURCES}
        for r in REGIMES
    }
    regime_total = {
        r: {s: 0 for s in SOURCES}
        for r in REGIMES
    }

    counted = 0
    no_breakdown = 0
    no_pnl = 0

    for t in trades:
        if not isinstance(t, dict):
            continue
        if t.get("pnl_usd") is None:
            no_pnl += 1
            continue
        pairs = collect_from_trade(t)
        if not pairs:
            no_breakdown += 1
            continue
        for src, agreed, won, regime in pairs:
            if regime not in REGIMES:
                regime = "unknown"
            base_total[src] += 1
            regime_total[regime][src] += 1
            if agreed == won:
                base_hits[src] += 1
                regime_hits[regime][src] += 1
        counted += 1

    log.info(
        "trades with breakdown: %d / %d",
        counted, len(trades),
    )
    log.info(
        "  no pnl: %d, no breakdown: %d",
        no_pnl, no_breakdown,
    )

    if counted == 0:
        log.info("no usable trades, skip")
        return

    weights = load_json(WEIGHTS_FILE, {})
    if not weights:
        log.warning("weights.json empty, skip")
        return

    # --- Base weights ---
    base_changed = []
    for src in SOURCES:
        total = base_total[src]
        if total < MIN_SAMPLES:
            log.info(
                "%s: base samples %d < %d, skip",
                src, total, MIN_SAMPLES,
            )
            continue
        w_old = float(weights.get(src, 1.0))
        upd = compute_update(
            w_old, base_hits[src], total,
        )
        if upd is None:
            continue
        w_new, hit_rate = upd
        if abs(w_new - w_old) > 1e-6:
            weights[src] = w_new
            base_changed.append(
                (src, w_old, w_new, total, hit_rate)
            )

    if base_changed:
        log.info("--- base weights ---")
        for src, wo, wn, n, hr in base_changed:
            log.info(
                "%s: %.4f -> %.4f "
                "(n=%d hit=%.2f)",
                src, wo, wn, n, hr,
            )

    # --- Per-regime weights ---
    regime_weights = weights.get(
        "regime_weights", {}
    )
    if not isinstance(regime_weights, dict):
        regime_weights = {}

    regime_changed = []

    for regime in REGIMES:
        section = regime_weights.get(regime, {})
        if not isinstance(section, dict):
            section = {}
        for src in SOURCES:
            total = regime_total[regime][src]
            if total < MIN_SAMPLES:
                continue
            w_old = float(
                section.get(
                    src, weights.get(src, 1.0)
                )
            )
            upd = compute_update(
                w_old,
                regime_hits[regime][src],
                total,
            )
            if upd is None:
                continue
            w_new, hit_rate = upd
            if abs(w_new - w_old) > 1e-6:
                section[src] = w_new
                regime_changed.append(
                    (regime, src, w_old, w_new,
                     total, hit_rate)
                )
        if section:
            regime_weights[regime] = section

    if regime_changed:
        log.info("--- regime weights ---")
        for reg, src, wo, wn, n, hr in regime_changed:
            log.info(
                "[%s] %s: %.4f -> %.4f "
                "(n=%d hit=%.2f)",
                reg, src, wo, wn, n, hr,
            )

    if not base_changed and not regime_changed:
        log.info(
            "no updates "
            "(all sources below MIN_SAMPLES=%d)",
            MIN_SAMPLES,
        )
        return

    weights["regime_weights"] = regime_weights
    weights["updated_at"] = datetime.now(
        timezone.utc
    ).isoformat()
    weights["total_trades_used"] = counted
    save_json(WEIGHTS_FILE, weights)

    log.info("weights saved: %s", WEIGHTS_FILE.name)
    log.info("=" * 60)


if __name__ == "__main__":
    main()