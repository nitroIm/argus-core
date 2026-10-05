# ============================================================
# ARGUS-Trader - SIGNALS v2 [PRODUCTION]
# ------------------------------------------------------------
# v2: propagate objective, model_version, predicted_return_pct
#     from last_predictions.json.
#     Needed by notify v2 — otherwise those fields dead code.
#     + sanity on prob_up/confidence in [0,1].
# v1: initial.
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

BUY_CONF = 0.30
STRONG_BUY = 0.50


def _clip01(v):
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def classify(prob_up, conf):
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
    log.info("ARGUS-Trader SIGNALS v2")
    log.info("=" * 60)

    if not PRED_FILE.exists():
        log.error("no predictions: %s", PRED_FILE)
        return

    with open(
        PRED_FILE, "r", encoding="utf-8"
    ) as f:
        data = json.load(f)

    signals = []
    for pred in data.get("predictions", []):
        symbol = pred.get("symbol")
        if not symbol:
            continue

        prob_up = _clip01(
            float(
                pred.get("prob_up", 0.5) or 0.5
            )
        )
        conf = _clip01(
            float(
                pred.get("confidence", 0) or 0
            )
        )

        action = classify(prob_up, conf)
        out = {
            "symbol": symbol,
            "action": action,
            "strength": strength(conf),
            "prob_up": round(prob_up, 4),
            "confidence": round(conf, 4),
        }

        # propagate optional fields for notify v2
        for k in (
            "objective",
            "model_version",
            "predicted_return_pct",
        ):
            if k in pred:
                out[k] = pred[k]

        signals.append(out)

        extra = ""
        if "predicted_return_pct" in out:
            extra = " ret=%.4f%%" % (
                out["predicted_return_pct"],
            )
        log.info(
            "%s: %s [%s] prob_up=%.4f "
            "conf=%.4f%s",
            symbol, action, out["strength"],
            prob_up, conf, extra,
        )

    result = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "model_accuracy": data.get(
            "model_accuracy"
        ),
        "signals": signals,
    }

    with open(
        SIGNALS_FILE, "w", encoding="utf-8"
    ) as f:
        json.dump(
            result, f,
            ensure_ascii=False, indent=2,
        )
    log.info("saved: %s", SIGNALS_FILE.name)


if __name__ == "__main__":
    main()