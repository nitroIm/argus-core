# ============================================================
# ARGUS-Trader - EXPORT v2 [PRODUCTION]
# ------------------------------------------------------------
# v2: экспорт только внутренних колонок features
# v1: базовый экспорт
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
    FEATURE_COLS, EXTERNAL_COLS, TARGET_COL,
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


def export_model():
    src = MODELS_DIR / "lgb_model.txt"
    if not src.exists():
        log.warning("no model file")
        return
    dst = EXPORT_DIR / "lgb_model.txt"
    shutil.copy2(src, dst)
    log.info("model -> %s", dst.name)


def export_meta():
    src = MODELS_DIR / "model_meta.json"
    if not src.exists():
        log.warning("no meta file")
        return
    dst = EXPORT_DIR / "model_meta.json"
    with open(src, "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta["exported_at"] = datetime.now(
        timezone.utc
    ).isoformat()
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    log.info("meta -> %s", dst.name)


def export_dataset_csv():
    """Экспорт features_hourly + external CSV."""
    # 1. Внутренние колонки из features_hourly
    internal = [
        c for c in FEATURE_COLS
        if c not in EXTERNAL_COLS
    ]
    cols1 = (
        ["symbol", "timestamp"]
        + internal
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

    # 2. External отдельно
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
        "# ARGUS ML - Export v2",
        "",
        "## Файлы",
        "- `lgb_model.txt` - LightGBM модель",
        "- `model_meta.json` - метрики + features",
        "- `features_hourly.csv` - основные данные",
        "- `external_market.csv` - DXY/SPX/GOLD",
        "- `README.md`",
        "",
        "## Как использовать",
        "1. pip install lightgbm==4.5.0",
        "2. model = lgb.Booster(model_file='lgb_model.txt')",
        "3. Объединить features + external по timestamp",
        "4. Признаки (25, порядок важен):",
        ", ".join(FEATURE_COLS),
        "",
        "5. Target: " + TARGET_COL + " (0/1)",
        "",
        "## Заметка",
        "Модель переносима. Обе таблицы обязательны.",
    ]
    with open(readme, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info("README -> %s", readme.name)


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader EXPORT v2")
    log.info("=" * 60)

    export_model()
    export_meta()
    export_dataset_csv()
    export_readme()

    log.info("done -> %s", EXPORT_DIR)


if __name__ == "__main__":
    main()