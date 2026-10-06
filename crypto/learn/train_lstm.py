# ============================================================
# ARGUS-Trader - TRAIN LSTM v2
# ------------------------------------------------------------
# v2: winsorize y to +-20. DROPOUT 0.4. WD 0.001.
#     Logs clipped count. Reduces train/test IC gap.
# v1: LSTM on 50-bar sequences. Per-symbol.
# ============================================================

import os
os.environ.setdefault("USE_CROSS", "0")

import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import joblib
import torch
import torch.nn as nn
from sklearn.preprocessing import StandardScaler

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
log = logging.getLogger("crypto.learn.train_lstm")

MODELS_DIR = SCRIPT_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

SEQ_LEN = 50
HIDDEN = 32
LAYERS = 2
DROPOUT = 0.4

EPOCHS = 60
BATCH = 64
LR = 0.001
WD = 0.001
PATIENCE = 10
Y_CLIP = 20.0

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


class LSTMModel(nn.Module):
    def __init__(self, n_feat):
        super().__init__()
        self.lstm = nn.LSTM(
            n_feat,
            HIDDEN,
            num_layers=LAYERS,
            batch_first=True,
            dropout=DROPOUT,
        )
        self.head = nn.Sequential(
            nn.Dropout(DROPOUT),
            nn.Linear(HIDDEN, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
        )

    def forward(self, x):
        out, _ = self.lstm(x)
        last = out[:, -1, :]
        return self.head(last).squeeze(-1)


def _ic(y_true, y_pred):
    if len(y_true) < 10:
        return 0.0
    if np.std(y_true) == 0:
        return 0.0
    if np.std(y_pred) == 0:
        return 0.0
    yt = y_true - y_true.mean()
    yp = y_pred - y_pred.mean()
    d = (yt * yt).sum() * (yp * yp).sum()
    if d <= 0:
        return 0.0
    d = float(np.sqrt(d))
    if d == 0:
        return 0.0
    return float((yt * yp).sum() / d)


def _nan_safe(X):
    return np.nan_to_num(
        X,
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )


def _clip_y(y):
    return np.clip(y, -Y_CLIP, Y_CLIP)


def _sequences(X, y, seq_len):
    n = len(X)
    if n < seq_len + 10:
        return None, None
    xs = []
    ys = []
    for i in range(seq_len - 1, n):
        xs.append(X[i - seq_len + 1:i + 1])
        ys.append(y[i])
    return (
        np.asarray(xs, dtype=np.float32),
        np.asarray(ys, dtype=np.float32),
    )


def _train_epochs(model, Xtr, ytr, Xva, yva):
    opt = torch.optim.Adam(
        model.parameters(),
        lr=LR,
        weight_decay=WD,
    )
    loss_fn = nn.MSELoss()
    best_val = float("inf")
    best_state = None
    bad = 0

    Xtr_t = torch.from_numpy(Xtr)
    ytr_t = torch.from_numpy(ytr)
    Xva_t = torch.from_numpy(Xva)
    yva_t = torch.from_numpy(yva)

    n = len(Xtr_t)

    for ep in range(1, EPOCHS + 1):
        model.train()
        perm = torch.randperm(n)
        total = 0.0
        for i in range(0, n, BATCH):
            idx = perm[i:i + BATCH]
            xb = Xtr_t[idx]
            yb = ytr_t[idx]
            opt.zero_grad()
            pred = model(xb)
            loss = loss_fn(pred, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                model.parameters(), 1.0
            )
            opt.step()
            total += float(loss) * len(idx)
        train_loss = total / n

        model.eval()
        with torch.no_grad():
            val_pred = model(Xva_t)
            val_loss = float(loss_fn(
                val_pred, yva_t
            ))

        if ep % 5 == 0 or ep == 1:
            log.info(
                "  ep %d train=%.5f val=%.5f",
                ep, train_loss, val_loss,
            )

        if val_loss < best_val - 1e-6:
            best_val = val_loss
            best_state = {
                k: v.clone()
                for k, v in model.state_dict().items()
            }
            bad = 0
        else:
            bad += 1
            if bad >= PATIENCE:
                log.info(
                    "  early stop at ep %d", ep
                )
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    return model


def train_one(symbol):
    log.info("-" * 60)
    log.info("TRAIN LSTM %s", symbol)
    log.info("-" * 60)

    ds.SYMBOLS = [symbol]
    ds.REFERENCE = symbol
    if symbol in DB2_SET:
        ds.DB2_SYMBOLS = {symbol}
    else:
        ds.DB2_SYMBOLS = set()

    data = ds.prepare()
    if data is None:
        log.error("%s: no data", symbol)
        return None

    X_train = _nan_safe(data["X_train"])
    X_test = _nan_safe(data["X_test"])
    r_train = data["r_train"]
    r_test = data["r_test"]

    n_clipped = int(
        (np.abs(r_train) > Y_CLIP).sum()
    )
    r_train_c = _clip_y(r_train)
    r_test_c = _clip_y(r_test)

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_train)
    X_te_s = scaler.transform(X_test)

    n_tr = len(X_tr_s)
    cut = int(n_tr * 0.85)

    X_tr = X_tr_s[:cut]
    y_tr = r_train_c[:cut]
    X_va = X_tr_s[cut:]
    y_va = r_train_c[cut:]

    Xtr_s, ytr_s = _sequences(
        X_tr, y_tr, SEQ_LEN
    )
    Xva_s, yva_s = _sequences(
        X_va, y_va, SEQ_LEN
    )
    Xte_s, yte_s = _sequences(
        X_te_s, r_test_c, SEQ_LEN
    )

    if Xtr_s is None or Xte_s is None:
        log.error("%s: seq too short", symbol)
        return None

    log.info(
        "%s: tr=%s va=%s te=%s feat=%d clipped=%d",
        symbol,
        Xtr_s.shape, Xva_s.shape,
        Xte_s.shape, Xtr_s.shape[2],
        n_clipped,
    )

    torch.manual_seed(42)
    np.random.seed(42)

    model = LSTMModel(Xtr_s.shape[2])
    model = _train_epochs(
        model, Xtr_s, ytr_s, Xva_s, yva_s
    )

    model.eval()
    with torch.no_grad():
        p_tr = model(
            torch.from_numpy(Xtr_s)
        ).numpy().astype(np.float32)
        p_te = model(
            torch.from_numpy(Xte_s)
        ).numpy().astype(np.float32)

    ic_tr = _ic(ytr_s, p_tr)
    ic_te = _ic(yte_s, p_te)
    mae_te = float(
        np.mean(np.abs(p_te - yte_s))
    )
    rmse_te = float(
        np.sqrt(np.mean((p_te - yte_s) ** 2))
    )

    log.info(
        "%s: test: IC=%.4f MAE=%.4f RMSE=%.4f",
        symbol, ic_te, mae_te, rmse_te,
    )
    log.info(
        "%s: train: IC=%.4f", symbol, ic_tr,
    )

    model_path = MODELS_DIR / (
        "lstm_" + symbol + ".pt"
    )
    scaler_path = MODELS_DIR / (
        "scaler_lstm_" + symbol + ".joblib"
    )
    torch.save(
        {
            "state": model.state_dict(),
            "n_feat": Xtr_s.shape[2],
            "seq_len": SEQ_LEN,
            "hidden": HIDDEN,
            "layers": LAYERS,
            "dropout": DROPOUT,
        },
        str(model_path),
    )
    joblib.dump(scaler, str(scaler_path))
    log.info("saved %s", model_path.name)

    meta = {
        "trained_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "version": "v2-lstm",
        "objective": "regression",
        "algorithm": "lstm",
        "symbol": symbol,
        "seq_len": SEQ_LEN,
        "hidden": HIDDEN,
        "layers": LAYERS,
        "dropout": DROPOUT,
        "y_clip": Y_CLIP,
        "n_clipped": n_clipped,
        "n_train": data["n_train"],
        "n_test": data["n_test"],
        "n_seq_train": int(Xtr_s.shape[0]),
        "n_seq_test": int(Xte_s.shape[0]),
        "ic_train": round(ic_tr, 4),
        "ic_test": round(ic_te, 4),
        "mae_test": round(mae_te, 4),
        "rmse_test": round(rmse_te, 4),
        "features": data["feature_cols"],
        "horizon": data["horizon"],
    }
    meta_path = MODELS_DIR / (
        "meta_lstm_" + symbol + ".json"
    )
    with open(
        meta_path, "w", encoding="utf-8"
    ) as f:
        json.dump(
            meta, f,
            ensure_ascii=False, indent=2,
        )
    log.info("saved %s", meta_path.name)

    return meta


def train():
    log.info("=" * 60)
    log.info("ARGUS-Trader TRAIN LSTM v2")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info(
        "SEQ_LEN=%d HIDDEN=%d LAYERS=%d DROP=%.1f",
        SEQ_LEN, HIDDEN, LAYERS, DROPOUT,
    )
    log.info(
        "EPOCHS=%d BATCH=%d LR=%.4f WD=%.4f",
        EPOCHS, BATCH, LR, WD,
    )
    log.info(
        "PATIENCE=%d Y_CLIP=+-%.1f",
        PATIENCE, Y_CLIP,
    )
    log.info("=" * 60)

    metas = {}
    for sym in SYMBOLS_LIST:
        try:
            m = train_one(sym)
            if m:
                metas[sym] = m
        except Exception as exc:
            log.error("%s: %s", sym, exc)

    log.info("=" * 60)
    log.info("LSTM TRAIN DONE")
    for sym, m in metas.items():
        log.info(
            "  %s: IC=%.4f RMSE=%.4f seq_tr=%d",
            sym, m["ic_test"],
            m["rmse_test"],
            m["n_seq_train"],
        )
    if metas:
        avg = sum(
            m["ic_test"] for m in metas.values()
        ) / len(metas)
        log.info(
            "  AVG IC=%.4f (%d models)",
            avg, len(metas),
        )
    log.info("=" * 60)

    ds.SYMBOLS = ["BTCUSDT", "ETHUSDT"]
    ds.REFERENCE = "BTCUSDT"
    ds.DB2_SYMBOLS = DB2_SET

    return metas


def main():
    metas = train()
    if not metas:
        log.error("lstm train failed")
        return
    log.info("DONE. %d lstm models", len(metas))


if __name__ == "__main__":
    main()