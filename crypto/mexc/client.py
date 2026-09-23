# ============================================================
# ARGUS - MEXC CLIENT v1 [PRODUCTION]
# ------------------------------------------------------------
# Базовый клиент для MEXC API v3.
# Подпись запросов, GET/POST/DELETE.
# ------------------------------------------------------------
# Требования:
#   pip install requests
# ============================================================

import os
import time
import hmac
import hashlib
import logging
import requests
from urllib.parse import urlencode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mexc.client")

BASE_URL = "https://api.mexc.com"
TIMEOUT = 15
RECV_WINDOW = 5000

API_KEY = os.getenv("MEXC_API_KEY", "").strip()
API_SECRET = os.getenv("MEXC_API_SECRET", "").strip()


class MexcClient:
    """Клиент MEXC API v3."""

    def __init__(self, api_key=None, api_secret=None):
        self.api_key = api_key or API_KEY
        secret = api_secret or API_SECRET
        self.api_secret = secret.encode("utf-8")

    def _sign(self, params):
        query = urlencode(params)
        sig = hmac.new(
            self.api_secret,
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return sig

    def _headers(self):
        return {
            "X-MEXC-APIKEY": self.api_key,
            "Content-Type": "application/json",
        }

    def public_get(self, path, params=None):
        url = BASE_URL + path
        try:
            r = requests.get(
                url, params=params, timeout=TIMEOUT,
            )
            if r.status_code == 200:
                return r.json()
            log.warning(
                "GET %s -> %d: %s",
                path, r.status_code, r.text[:200],
            )
        except Exception as e:
            log.error("GET %s: %s", path, e)
        return None

    def signed_get(self, path, params=None):
        if not self.api_key or not self.api_secret:
            log.error("API key/secret not set")
            return None

        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = RECV_WINDOW
        params["signature"] = self._sign(params)

        url = BASE_URL + path
        try:
            r = requests.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                return r.json()
            log.warning(
                "GET %s -> %d: %s",
                path, r.status_code, r.text[:200],
            )
        except Exception as e:
            log.error("GET %s: %s", path, e)
        return None

    def signed_post(self, path, params=None):
        if not self.api_key or not self.api_secret:
            log.error("API key/secret not set")
            return None

        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = RECV_WINDOW
        params["signature"] = self._sign(params)

        url = BASE_URL + path
        try:
            r = requests.post(
                url,
                params=params,
                headers=self._headers(),
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                return r.json()
            log.warning(
                "POST %s -> %d: %s",
                path, r.status_code, r.text[:200],
            )
        except Exception as e:
            log.error("POST %s: %s", path, e)
        return None

    def signed_delete(self, path, params=None):
        if not self.api_key or not self.api_secret:
            log.error("API key/secret not set")
            return None

        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = RECV_WINDOW
        params["signature"] = self._sign(params)

        url = BASE_URL + path
        try:
            r = requests.delete(
                url,
                params=params,
                headers=self._headers(),
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                return r.json()
            log.warning(
                "DELETE %s -> %d: %s",
                path, r.status_code, r.text[:200],
            )
        except Exception as e:
            log.error("DELETE %s: %s", path, e)
        return None


def is_configured():
    return bool(API_KEY and API_SECRET)