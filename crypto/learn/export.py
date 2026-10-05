# ============================================================
# ARGUS-Trader - EXPORT v4 [PRODUCTION]
# ------------------------------------------------------------
# v4: экспорт всех per-symbol моделей (lgb_{sym}.txt
#     + meta_{sym}.json). Плюс совместимость:
#     lgb_model.txt / model_meta.json = BTC.
# v3: экспорт только INTERNAL_COLS.
# ============================================================

import sys
import csv
import json
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from db import get_connection
from dataset import (
    FEATURE_COLS, INTERNAL_COLS,
    EXTERNAL_COLS, TARGET_COL,
    HORIZON, MOVE_THRESHOLD_PCT, REFERENCE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.export")

MODELS_DIR = SCRIPT_DIR / "models"
EXPORT_DIR = SCRIPT_DIR / "export"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS_LIST = [
    "BTCUSDT", "ETHUSDT",
    "SOLUSDT", "BNBUSDT",
]


def export_all_models():
    """Копирует lgb_{sym}.txt + meta_{sym}.json."""
    exported = 0
    for sym in SYMBOLS_LIST:
        src_m = MODELS_DIR / ("lgb_" + sym + ".txt")
        src_mt = MODELS_DIR / ("meta_" + sym + ".json")
        if not src_m.exists():
            log.warning("%s: model missing", sym)
            continue
        dst_m = EXPORT_DIR / ("lgb_" + sym + ".txt")
        shutil.copy2(src_m, dst_m)
        if src_mt.exists():
            with open(src_mt, "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta["exported_at"] = datetime.now(
                timezone.utc
            ).isoformat()
            dst_mt = EXPORT_DIR / (
                "meta_" + sym + ".json"
            )
            with open(dst_mt, "w",
                      encoding="utf-8") as f:
                json.dump(meta, f,
                          ensure_ascii=False, indent=2)
        log.info("model -> %s", dst_m.name)
        exported += 1

    # Совместимость: lgb_model.txt = BTC
    btc_m = MODELS_DIR / "lgb_BTCUSDT.txt"
    btc_mt = MODELS_DIR / "meta_BTCUSDT.json"
    if btc_m.exists():
        shutil.copy2(
            btc_m, EXPORT_DIR / "lgb_model.txt"
        )
        log.info("compat: lgb_model.txt <- BTC")
    if btc_mt.exists():
        with open(btc_mt, "r", encoding="utf-8") as f:
            meta = json.load(f)
        meta["exported_at"] = datetime.now(
            timezone.utc
        ).isoformat()
        with open(
            EXPORT_DIR / "model_meta.json",
            "w", encoding="utf-8",
        ) as f:
            json.dump(meta, f,
                      ensure_ascii=False, indent=2)
        log.info("compat: model_meta.json <- BTC")
    return exported


def export_dataset_csv():
    cols1 = (
        ["symbol", "timestamp"]
        + INTERNAL_COLS
        + [TARGET_COL]
    )
    out1 = EXPORT_DIR / "features_hourly.csv"
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT " + ", ".join(cols1)
                    + " FROM features_hourly "
                    + "ORDER BY timestamp"
                )
                rows = cur.fetchall()
        with open(out1, "w", encoding="utf-8",
                  newline="") as f:
            w = csv.writer(f)
            w.writerow(cols1)
            for r in rows:
                w.writerow(r)
        log.info(
            "features -> %s (%d rows)",
            out1.name, len(rows),
        )
    except Exception as e:
        log.error("export features: %s", e)

    out2 = EXPORT_DIR / "external_market.csv"
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, timestamp, "
                    "close, change_pct "
                    "FROM external_market "
                    "ORDER BY symbol, timestamp"
                )
                rows = cur.fetchall()
        with open(out2, "w", encoding="utf-8",
                  newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "symbol", "timestamp",
                "close", "change_pct",
            ])
            for r in rows:
                w.writerow(r)
        log.info(
            "external -> %s (%d rows)",
            out2.name, len(rows),
        )
    except Exception as e:
        log.error("export external: %s", e)


def export_readme():
    readme = EXPORT_DIR / "README.md"
    lines = [
        "# ARGUS ML - Export v4",
        "",
        "## Per-symbol модели",
        "- `lgb_BTCUSDT.txt` + `meta_BTCUSDT.json`",
        "- `lgb_ETHUSDT.txt` + `meta_ETHUSDT.json`",
        "- `lgb_SOLUSDT.txt` + `meta_SOLUSDT.json`",
        "- `lgb_BNBUSDT.txt` + `meta_BNBUSDT.json`",
        "",
        "## Совместимость",
        "- `lgb_model.txt` = копия BTC-модели",
        "- `model_meta.json` = копия meta BTC",
        "",
        "## Данные",
        "- `features_hourly.csv` - внутренние фичи",
        "- `external_market.csv` - DXY/SPX/GOLD",
        "",
        "## Параметры",
        "- HORIZON: " + str(HORIZON) + "h",
        "- THRESHOLD: " + str(MOVE_THRESHOLD_PCT) + "%",
        "- REFERENCE: " + REFERENCE,
        "- FEATURE_COLS: " + str(len(FEATURE_COLS)),
        "",
        "## Использование",
        "1. pip install lightgbm==4.5.0",
        "2. model = lgb.Booster(",
        "     model_file='lgb_BTCUSDT.txt')",
    ]
    with open(readme, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info("README -> %s", readme.name)


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader EXPORT v4")
    log.info(
        "FEATURE_COLS=%d INTERNAL=%d EXTERNAL=%d",
        len(FEATURE_COLS),
        len(INTERNAL_COLS),
        len(EXTERNAL_COLS),
    )
    log.info("=" * 60)

    n = export_all_models()
    export_dataset_csv()
    export_readme()

    log.info("done: %d models -> %s", n, EXPORT_DIR)


if __name__ == "__main__":
    main()