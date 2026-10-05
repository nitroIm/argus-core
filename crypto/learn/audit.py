# ============================================================
# ARGUS-Trader - FEATURE AUDIT
# ------------------------------------------------------------
# v1: per-feature IC vs target on train split.
#     Detects look-ahead leak. Any single feature with
#     |IC| > 0.15 on 12h return is suspicious.
# ============================================================

import os
os.environ.setdefault("USE_CROSS", "0")

import sys
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
log = logging.getLogger("crypto.learn.audit")

DB2_SET = {
    s.strip().upper()
    for s in (
        os.getenv("DB2_SYMBOLS")
        or "SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
}


def ic(a, b):
    m = ~(np.isnan(a) | np.isnan(b))
    a = a[m].astype(np.float64)
    b = b[m].astype(np.float64)
    if len(a) < 10:
        return 0.0
    a = a - a.mean()
    b = b - b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    if d == 0:
        return 0.0
    return float((a * b).sum() / d)


def audit_one(sym):
    ds.SYMBOLS = [sym]
    ds.REFERENCE = sym
    ds.DB2_SYMBOLS = (
        {sym} if sym in DB2_SET else set()
    )

    data = ds.prepare()
    if data is None:
        log.error("%s: no data", sym)
        return

    X = data["X_train"].astype(np.float64)
    y = data["r_train"].astype(np.float64)
    cols = data["feature_cols"]

    log.info("=" * 60)
    log.info("AUDIT %s: n=%d", sym, len(y))
    log.info("=" * 60)

    res = []
    for i, name in enumerate(cols):
        v = X[:, i]
        res.append((name, ic(v, y)))
    res.sort(key=lambda t: -abs(t[1]))

    for name, v_ic in res:
        flag = "  <-- LEAK?" if abs(v_ic) > 0.15 else ""
        log.info(
            "  %-24s IC=%+.4f%s",
            name, v_ic, flag,
        )


def main():
    syms = [
        s.strip().upper()
        for s in (
            os.getenv("SYMBOLS")
            or "BTCUSDT"
        ).split(",")
        if s.strip()
    ]
    for s in syms:
        try:
            audit_one(s)
        except Exception as e:
            log.error("%s: %s", s, e)


if __name__ == "__main__":
    main()