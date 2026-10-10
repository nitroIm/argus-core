# ============================================================
# ARGUS-Trader - TRAIN v17 (classification)
# ------------------------------------------------------------
# v17: binary classification (direction).
#      Objective: binary, metric: auc.
#      Early stopping by AUC.
#      Probability output (0..1).
#      No regression, no RMSE, no IC.
# ============================================================

import os
os.environ.setdefault("USE_CROSS", "0")

import sys
import json
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import lightgbm as lgb

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
log = logging.getLogger("crypto.learn.train")

MODELS_DIR = SCRIPT_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

PREV_DIR = MODELS_DIR / "prev"

MIN_SAMPLES = 200
NUM_ROUNDS = 2000
VAL_FRAC = 0.15
EARLY_STOP = 80

PARAMS_BASE = {
    "objective": "binary",
    "metric": "auc",
    "boosting_type": "gbdt",
    "num_leaves": 16,
    "max_depth": 4,
    "learning_rate": 0.02,
    "feature_fraction": 0.5,
    "bagging_fraction": 0.7,
    "bagging_freq": 5,
    "min_data_in_leaf": 100,
    "lambda_l1": 1.0,
    "lambda_l2": 1.0,
    "is_unbalance": False,
    "verbose": -1,
    "seed": 42,
}

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


def model_file(sym):
    return MODELS_DIR / ("lgb_" + sym + ".txt")


def meta_file(sym):
    return MODELS_DIR / ("meta_" + sym + ".json")


def prev_model_file(sym):
    return PREV_DIR / ("lgb_" + sym + ".txt")


def prev_meta_file(sym):
    return PREV_DIR / ("meta_" + sym + ".json")


def save_prev(sym):
    mf = model_file(sym)
    if not mf.exists():
        return
    PREV_DIR.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(mf, prev_model_file(sym))
        mfa = meta_file(sym)
        if mfa.exists():
            shutil.copy2(mfa, prev_meta_file(sym))
    except Exception as e:
        log.warning("prev save %s: %s", sym, e)


def _auc(y_true, y_pred):
    """Simple AUC via rank-based formula."""
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    n_pos = int((y_true == 1).sum())
    n_neg = int((y_true == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return 0.5
    order = np.argsort(y_pred)
    ranks = np.empty_like(order, dtype=np.float64)
    ranks[order] = np.arange(1, len(y_pred) + 1)
    # handle ties by averaging
    sp = y_pred[order]
    i = 0
    while i < len(sp):
        j = i
        while j + 1 < len(sp) and sp[j + 1] == sp[i]:
            j += 1
        if j > i:
            avg = (i + 1 + j + 1) / 2.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg
        i = j + 1
    sum_pos = ranks[y_true == 1].sum()
    auc = (
        sum_pos - n_pos * (n_pos + 1) / 2.0
    ) / (n_pos * n_neg)
    return float(auc)


def _acc(y_true, prob, thr=0.5):
    pred = (prob >= thr).astype(int)
    return float(np.mean(pred == y_true))


def train_one(symbol):
    log.info("-" * 60)
    log.info("TRAIN %s", symbol)
    log.info("-" * 60)

    ds.SYMBOLS = [symbol]
    ds.REFERENCE = symbol
    ds.DB2_SYMBOLS = (
        {symbol} if symbol in DB2_SET else set()
    )

    data = ds.prepare()
    if data is None:
        log.error("%s: no data", symbol)
        return None

    if data["n_train"] < MIN_SAMPLES:
        log.warning(
            "%s: not enough samples: %d",
            symbol, data["n_train"],
        )

    X_train = data["X_train"]
    X_test = data["X_test"]
    y_train = data["y_train"]
    y_test = data["y_test"]

    n_tr = len(X_train)
    cut = int(n_tr * (1 - VAL_FRAC))

    purge = ds.PURGE_HOURS
    cut_clean = max(cut - purge, int(n_tr * 0.5))

    X_tr = X_train[:cut_clean]
    y_tr = y_train[:cut_clean]
    X_va = X_train[cut:]
    y_va = y_train[cut:]

    log.info(
        "%s: train=%d val=%d test=%d (gap=%dh)",
        symbol, len(X_tr), len(X_va),
        len(X_test), cut - cut_clean,
    )

    train_set = lgb.Dataset(X_tr, label=y_tr)
    val_set = lgb.Dataset(
        X_va, label=y_va, reference=train_set,
    )

    model = lgb.train(
        PARAMS_BASE, train_set,
        num_boost_round=NUM_ROUNDS,
        valid_sets=[val_set],
        callbacks=[
            lgb.early_stopping(
                EARLY_STOP, verbose=False,
            ),
            lgb.log_evaluation(100),
        ],
    )

    best_iter = (
        model.best_iteration
        or model.num_trees()
    )
    log.info(
        "%s: best_iteration=%d (of %d)",
        symbol, best_iter, NUM_ROUNDS,
    )

    p_tr = model.predict(X_tr).astype(np.float32)
    p_va = model.predict(X_va).astype(np.float32)
    p_te = model.predict(X_test).astype(np.float32)

    auc_tr = _auc(y_tr, p_tr)
    auc_va = _auc(y_va, p_va)
    auc_te = _auc(y_test, p_te)

    acc_tr = _acc(y_tr, p_tr, 0.5)
    acc_va = _acc(y_va, p_va, 0.5)
    acc_te = _acc(y_test, p_te, 0.5)

    # high-confidence subset
    def conf_stats(y, p, thr):
        m = np.abs(p - 0.5) >= (thr - 0.5)
        if m.sum() < 10:
            return 0.0, 0.0, 0
        acc = float(np.mean(
            ((p[m] >= 0.5).astype(int)) == y[m]
        ))
        cov = float(m.mean())
        return acc, cov, int(m.sum())

    a60_tr, c60_tr, n60_tr = conf_stats(y_tr, p_tr, 0.60)
    a60_te, c60_te, n60_te = conf_stats(y_test, p_te, 0.60)
    a65_te, c65_te, n65_te = conf_stats(y_test, p_te, 0.65)

    log.info(
        "%s: AUC  tr=%.4f va=%.4f te=%.4f",
        symbol, auc_tr, auc_va, auc_te,
    )
    log.info(
        "%s: ACC  tr=%.4f va=%.4f te=%.4f",
        symbol, acc_tr, acc_va, acc_te,
    )
    log.info(
        "%s: |p-0.5|>=0.10  acc_te=%.4f cov=%.2f n=%d",
        symbol, a60_te, c60_te, n60_te,
    )
    log.info(
        "%s: |p-0.5|>=0.15  acc_te=%.4f cov=%.2f n=%d",
        symbol, a65_te, c65_te, n65_te,
    )

    importance = model.feature_importance(
        importance_type="gain"
    )
    feat_names = data["feature_cols"]
    pairs = sorted(
        zip(feat_names, importance),
        key=lambda x: -x[1],
    )
    log.info("%s: top features:", symbol)
    for name, score in pairs[:5]:
        log.info("  %s: %.2f", name, score)

    save_prev(symbol)
    model.save_model(str(model_file(symbol)))
    log.info(
        "%s: saved %s",
        symbol, model_file(symbol).name,
    )

    meta = {
        "trained_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "version": "v17",
        "objective": "binary",
        "symbol": symbol,
        "n_total": data["n_total"],
        "n_train": data["n_train"],
        "n_test": data["n_test"],
        "n_tr_inner": len(X_tr),
        "n_val_inner": len(X_va),
        "purge_hours": int(purge),
        "gap_tr_va": int(cut - cut_clean),
        "class_thr_pct": data["class_thr_pct"],
        "best_iteration": best_iter,
        "auc_train": round(auc_tr, 4),
        "auc_val": round(auc_va, 4),
        "auc_test": round(auc_te, 4),
        "acc_train": round(acc_tr, 4),
        "acc_val": round(acc_va, 4),
        "acc_test": round(acc_te, 4),
        "conf60_acc_te": round(a60_te, 4),
        "conf60_cov_te": round(c60_te, 4),
        "conf60_n_te": n60_te,
        "conf65_acc_te": round(a65_te, 4),
        "conf65_cov_te": round(c65_te, 4),
        "conf65_n_te": n65_te,
        "num_trees": model.num_trees(),
        "features": feat_names,
        "top_features": [
            {"name": n, "gain": round(float(s), 2)}
            for n, s in pairs[:10]
        ],
        "horizon": data["horizon"],
        "params_used": PARAMS_BASE,
        "num_rounds": NUM_ROUNDS,
        "early_stop": EARLY_STOP,
        # compat keys for predict.py / backtest.py
        "ic_test": round(
            2.0 * (auc_te - 0.5), 4
        ),
        "ic_val": round(
            2.0 * (auc_va - 0.5), 4
        ),
        "ic_train": round(
            2.0 * (auc_tr - 0.5), 4
        ),
        "sign_acc_test": round(acc_te, 4),
        "sign_acc_val": round(acc_va, 4),
        "sign_acc_train": round(acc_tr, 4),
    }
    with open(
        meta_file(symbol), "w", encoding="utf-8",
    ) as f:
        json.dump(
            meta, f,
            ensure_ascii=False, indent=2,
        )

    return meta


def write_compat(symbols, metas):
    ref = (
        "BTCUSDT"
        if "BTCUSDT" in symbols else symbols[0]
    )
    src = model_file(ref)
    if src.exists():
        shutil.copy2(
            src, MODELS_DIR / "lgb_model.txt"
        )
    if metas.get(ref):
        with open(
            MODELS_DIR / "model_meta.json",
            "w", encoding="utf-8",
        ) as f:
            json.dump(
                metas[ref], f,
                ensure_ascii=False, indent=2,
            )


def train():
    log.info("=" * 60)
    log.info(
        "ARGUS-Trader TRAIN v17 (binary, AUC)"
    )
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("CLASS_THR_PCT=%.2f", ds.CLASS_THR_PCT)
    log.info(
        "BASE: rounds=%d early=%d val=%.2f",
        NUM_ROUNDS, EARLY_STOP, VAL_FRAC,
    )
    log.info("=" * 60)

    metas = {}
    for sym in SYMBOLS_LIST:
        try:
            m = train_one(sym)
            if m:
                metas[sym] = m
        except Exception as e:
            log.error("%s: %s", sym, e)

    log.info("=" * 60)
    log.info("TRAIN DONE")
    for sym, m in metas.items():
        log.info(
            "  %s: AUC_te=%.4f AUC_va=%.4f "
            "ACC_te=%.4f best_iter=%d",
            sym, m["auc_test"],
            m["auc_val"], m["acc_test"],
            m["best_iteration"],
        )
    if metas:
        avg_auc = (
            sum(m["auc_test"] for m in metas.values())
            / len(metas)
        )
        log.info(
            "  AVG AUC_test=%.4f (%d models)",
            avg_auc, len(metas),
        )
    log.info("=" * 60)

    if metas:
        write_compat(list(metas.keys()), metas)

    ds.SYMBOLS = ["BTCUSDT", "ETHUSDT"]
    ds.REFERENCE = "BTCUSDT"
    ds.DB2_SYMBOLS = DB2_SET

    return metas


def main():
    metas = train()
    if not metas:
        log.error("train failed")
        return
    log.info("DONE. %d models trained", len(metas))


if __name__ == "__main__":
    main()