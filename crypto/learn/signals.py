# ============================================================
# ARGUS-Trader - SIGNALS
# ------------------------------------------------------------
# Превращает предсказания в сигналы BUY/WAIT/SELL.
# Пока только лог + JSON (без ордеров).
# ============================================================

import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.signals")

PRED_FILE = SCRIPT_DIR / "last_predictions.json"
SIGNALS_FILE = SCRIPT_DIR / "last_signals.json"

# Пороги уверенности
BUY_CONF = 0.30      # prob_up > 0.65 или < 0.35
STRONG_BUY = 0.50    # prob_up > 0.75 или < 0.25


def classify(pred):
    prob_up = pred["prob_up"]
    conf = pred["confidence"]

    if conf < BUY_CONF:
        return "WAIT"
    if prob_up > 0.5:
        return "BUY"
    return "SELL"


def strength(conf):
    if conf >= STRONG_BUY:
        return "strong"
    if conf >= BUY_CONF:
        return "normal"
    return "weak"


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader SIGNALS")
    log.info("=" * 60)

    if not PRED_FILE.exists():
        log.error("no predictions: %s", PRED_FILE)
        return

    with open(PRED_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    signals = []
    for pred in data.get("predictions", []):
        action = classify(pred)
        signals.append({
            "symbol": pred["symbol"],
            "action": action,
            "strength": strength(pred["confidence"]),
            "prob_up": pred["prob_up"],
            "confidence": pred["confidence"],
        })
        log.info(
            "%s: %s [%s] prob_up=%.4f",
            pred["symbol"], action,
            signals[-1]["strength"],
            pred["prob_up"],
        )

    out = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "model_accuracy": data.get("model_accuracy"),
        "signals": signals,
    }

    with open(SIGNALS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info("saved: %s", SIGNALS_FILE.name)


if __name__ == "__main__":
    main()