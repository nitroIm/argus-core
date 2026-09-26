# ============================================================
# ARGUS-Trader - ROLLBACK [PRODUCTION]
# ------------------------------------------------------------
# Восстанавливает модель из prev/ одной командой.
# ============================================================

import sys
import shutil
import logging
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MODELS_DIR = SCRIPT_DIR / "models"
PREV_DIR = MODELS_DIR / "prev"

MODEL_FILE = MODELS_DIR / "lgb_model.txt"
META_FILE = MODELS_DIR / "model_meta.json"
PREV_MODEL = PREV_DIR / "lgb_model.txt"
PREV_META = PREV_DIR / "model_meta.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.rollback")


def rollback():
    log.info("=" * 50)
    log.info("ARGUS ROLLBACK")
    log.info("=" * 50)

    if not PREV_MODEL.exists():
        log.error("no prev model")
        return False

    try:
        shutil.copy2(PREV_MODEL, MODEL_FILE)
        log.info("model rolled back")
    except Exception as e:
        log.error("copy model: %s", e)
        return False

    if PREV_META.exists():
        try:
            shutil.copy2(PREV_META, META_FILE)
            log.info("meta rolled back")
        except Exception as e:
            log.warning("meta: %s", e)

    log.info("ROLLBACK DONE")
    return True


def main():
    ok = rollback()
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()