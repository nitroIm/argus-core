# ============================================================
# ARGUS-Trader - DIAGNOSE FEATURES
# ------------------------------------------------------------
# Compare train vs test per feature:
#   - NaN count
#   - min/max range
#   - count of test values outside train range
#   - count outside train 0.1%/99.9% quantiles
# Output: top-20 most problematic features per symbol.
# ============================================================

import os
os.environ.setdefault("USE_CROSS", "0")

import sys
import json
import logging
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import dataset as ds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.diagnose")

SYMBOLS_LIST = [
    s.strip().upper()
    for s in (
        os.getenv("SYMBOLS")
        or "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
]
DB2_SET = {
    s.strip().upper()
    for s in (
        os.getenv("DB2_SYMBOLS")
        or "SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
}

OUT_PATH = SCRIPT_DIR / "diagnose_report.json"


def _stats(arr):
    a = np.asarray(arr, dtype=np.float64)
    finite = np.isfinite(a)
    n_nan = int((~finite).sum())
    if finite.sum() == 0:
        return {
            "n": len(a),
            "nan": n_nan,
            "min": None,
            "max": None,
            "mean": None,
            "std": None,
            "q001": None,
            "q999": None,
        }
    vals = a[finite]
    return {
        "n": len(a),
        "nan": n_nan,
        "min": float(vals.min()),
        "max": float(vals.max()),
        "mean": float(vals.mean()),
        "std": float(vals.std()),
        "q001": float(np.quantile(vals, 0.001)),
        "q999": float(np.quantile(vals, 0.999)),
    }


def _outside(arr, lo, hi):
    if lo is None or hi is None:
        return 0
    a = np.asarray(arr, dtype=np.float64)
    finite = np.isfinite(a)
    if finite.sum() == 0:
        return 0
    v = a[finite]
    return int(((v < lo) | (v > hi)).sum())


def diagnose_symbol(sym, X_train, X_test, feats):
    log.info("-" * 60)
    log.info("DIAGNOSE %s train=%d test=%d",
             sym, len(X_train), len(X_test))
    log.info("-" * 60)

    n_feat = X_train.shape[1]
    rows = []

    for i in range(n_feat):
        name = feats[i] if i < len(feats) else ("f%d" % i)
        tr = X_train[:, i]
        te = X_test[:, i]

        st_tr = _stats(tr)
        st_te = _stats(te)

        if st_tr["min"] is None:
            rows.append({
                "feature": name,
                "index": i,
                "note": "no finite train",
                "nan_tr": st_tr["nan"],
                "nan_te": st_te["nan"],
                "out_range": 0,
                "out_q": 0,
                "drift": 0.0,
            })
            continue

        out_rng = _outside(
            te, st_tr["min"], st_tr["max"]
        )
        out_q = _outside(
            te, st_tr["q001"], st_tr["q999"]
        )

        drift = 0.0
        if st_tr["std"] and st_tr["std"] > 0:
            drift = abs(
                st_te["mean"] - st_tr["mean"]
            ) / st_tr["std"]

        rows.append({
            "feature": name,
            "index": i,
            "nan_tr": st_tr["nan"],
            "nan_te": st_te["nan"],
            "min_tr": st_tr["min"],
            "max_tr": st_tr["max"],
            "q001_tr": st_tr["q001"],
            "q999_tr": st_tr["q999"],
            "min_te": st_te["min"],
            "max_te": st_te["max"],
            "out_range": out_rng,
            "out_q": out_q,
            "drift": round(float(drift), 3),
        })

    rows.sort(
        key=lambda r: (
            r["out_range"],
            r["out_q"],
            r["drift"],
        ),
        reverse=True,
    )

    log.info("%s: top-20 problem features:", sym)
    log.info(
        "  %-22s %6s %6s %8s %8s %6s",
        "feature", "nan_tr", "nan_te",
        "out_rng", "out_q", "drift",
    )
    for r in rows[:20]:
        log.info(
            "  %-22s %6d %6d %8d %8d %6.2f",
            r["feature"][:22],
            r["nan_tr"], r["nan_te"],
            r["out_range"], r["out_q"],
            r["drift"],
        )

    nan_tr_total = sum(r["nan_tr"] for r in rows)
    nan_te_total = sum(r["nan_te"] for r in rows)
    log.info(
        "%s: total NaN cells tr=%d te=%d",
        sym, nan_tr_total, nan_te_total,
    )

    return {
        "symbol": sym,
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "n_features": int(n_feat),
        "nan_train": int(nan_tr_total),
        "nan_test": int(nan_te_total),
        "top_problems": rows[:20],
    }


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader DIAGNOSE FEATURES")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("=" * 60)

    ds.SYMBOLS = SYMBOLS_LIST
    ds.REFERENCE = SYMBOLS_LIST[0]
    ds.DB2_SYMBOLS = DB2_SET

    data = ds.prepare(test_frac=0.2)
    if data is None:
        log.error("prepare failed")
        return

    X_train = data["X_train"]
    X_test = data["X_test"]
    sym_test = data.get("sym_test")
    feats = data["feature_cols"]

    if sym_test is None:
        log.error("dataset missing sym_test")
        return

    sym_to_idx = {}
    for i, s in enumerate(sym_test):
        sym_to_idx.setdefault(s, []).append(i)

    report = {
        "n_features": len(feats),
        "features": feats,
        "symbols": [],
    }

    for sym in sorted(sym_to_idx.keys()):
        idxs = sym_to_idx[sym]
        Xs = X_test[idxs]

        # Split train rows belong to this symbol.
        # dataset gives us only X_train, but we
        # can re-split from full X per symbol.
        # Simpler: filter X_train by symbol col
        # — but prepare() did not return sym_train.
        # Fallback: use train rows from the
        # overall X_train proportional cut.
        # Here we approximate: for diagnosis
        # take test rows of this symbol vs
        # train rows of ALL symbols.
        # Better: report test-only stats vs
        # overall train stats. Good enough.
        info = diagnose_symbol(
            sym, X_train, Xs, feats,
        )
        report["symbols"].append(info)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(
            report, f,
            ensure_ascii=False, indent=2,
        )
    log.info("saved: %s", OUT_PATH.name)


if __name__ == "__main__":
    main()