# ============================================================
# ARGUS-Trader — NOTIFY ASIA + EUROPE (узел global)
# ------------------------------------------------------------
# v1: при |change_pct| > 2% за час → алерт в TG.
# ============================================================

import os
import sys
import logging
import requests
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from db2 import get_connection, close_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("global.notify")