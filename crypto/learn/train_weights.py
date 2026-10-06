# ============================================================
# ARGUS-Trader - ENSEMBLE WEIGHTS v3
# ------------------------------------------------------------
# v3: + lstm. 6 algos total.
# v2: + ridge, mlp. 5 algos.
# ============================================================

import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
MODELS_DIR = SCRIPT_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(
    "crypto.learn.train_weights"
)

ALGOS = ["lgb", "xgb", "cat", "ridge", "mlp", "lstm"]

SYMBOLS_LIST = [
    s.strip().upper()
    for s in (
        os.getenv("SYMBOLS")
        or "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
]


def _meta_path(sym, algo):
    if algo == "lgb":
        return MODELS_DIR / (
            "meta_" + sym + ".json"
        )
    return MODELS_DIR / (
        "meta_" + algo + "_" + sym + ".json"
    )


def _read_ic(sym, algo):
    p = _meta_path(sym, algo)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            meta = json.load(f)
        v = meta.get("ic_test")
        if v is None:
            return None
        return float(v)
    except Exception as exc:
        log.warning(
            "%s %s: %s", sym, algo, exc
        )
        return None


def compute_weights(sym):
    ics = {}
    for algo in ALGOS:
        ics[algo] = _read_ic(sym, algo)

    positives = {
        k: max(0.0, v)
        for k, v in ics.items()
        if v is not None
    }

    if not positives:
        log.warning("%s: no metas", sym)
        return None

    total = sum(positives.values())

    if total <= 0.0:
        n = len(positives)
        weights = {
            k: round(1.0 / n, 4)
            for k in positives
        }
        allowed = False
    else:
        weights = {
            k: round(v / total, 4)
            for k, v in positives.items()
        }
        allowed = True

    return {
        "ic": ics,
        "weights": weights,
        "trade_allowed": allowed,
    }


def main():
    log.info("=" * 60)
    log.info("ENSEMBLE WEIGHTS v3")
    log.info("ALGOS=%s", ALGOS)
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("=" * 60)

    out = {
        "computed_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "algos": ALGOS,
        "symbols": {},
    }

    for sym in SYMBOLS_LIST:
        r = compute_weights(sym)
        if r is None:
            continue
        out["symbols"][sym] = r

        ics = r["ic"]
        w = r["weights"]
        allowed = r["trade_allowed"]

        ics_str = " ".join(
            "%s=%s" % (a, ics.get(a))
            for a in ALGOS
        )
        log.info("%s: %s", sym, ics_str)

        w_str = " ".join(
            "%s=%.3f" % (a, w.get(a, 0.0))
            for a in ALGOS
        )
        log.info(
            "  weights: %s  allowed=%s",
            w_str, allowed,
        )

    out_path = MODELS_DIR / (
        "ensemble_weights.json"
    )
    with open(
        out_path, "w", encoding="utf-8"
    ) as f:
        json.dump(
            out, f,
            ensure_ascii=False, indent=2,
        )
    log.info("saved %s", out_path.name)


if __name__ == "__main__":
    main()