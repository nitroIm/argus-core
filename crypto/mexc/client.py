# ============================================================
# ARGUS - MEXC CLIENT v2 [PRODUCTION]
# ------------------------------------------------------------
# v2: retry, body для POST/DELETE,
#     обработка code в ответе, ключи в __init__.
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
RECV_WINDOW = 10000
MAX_RETRIES = 3
RETRY_SLEEP = 2


class MexcClient:
    """Клиент MEXC API v3."""

    def __init__(self, api_key=None, api_secret=None):
        self.api_key = (
            api_key
            or os.getenv("MEXC_API_KEY", "").strip()
        )
        secret = (
            api_secret
            or os.getenv(
                "MEXC_API_SECRET", ""
            ).strip()
        )
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

    def _check_response(self, data, path):
        """MEXC отдаёт 200 + code != 0 при ошибке."""
        if not isinstance(data, dict):
            return data
        code = data.get("code")
        if code is not None and code != 0:
            log.warning(
                "%s: code=%s msg=%s",
                path, code,
                data.get("msg", "?"),
            )
            return None
        return data

    def _request(self, method, path,
                 params=None, headers=None,
                 use_body=False):
        url = BASE_URL + path
        last_err = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if use_body and params:
                    kwargs = {
                        "data": urlencode(params),
                        "timeout": TIMEOUT,
                    }
                else:
                    kwargs = {
                        "params": params,
                        "timeout": TIMEOUT,
                    }
                if headers:
                    kwargs["headers"] = headers

                r = requests.request(
                    method, url, **kwargs,
                )

                if r.status_code == 200:
                    try:
                        return self._check_response(
                            r.json(), path,
                        )
                    except Exception:
                        log.error(
                            "%s: bad json", path,
                        )
                        return None

                # 5xx - retry
                if r.status_code >= 500:
                    last_err = r.status_code
                    log.warning(
                        "%s -> %d (try %d/%d)",
                        path, r.status_code,
                        attempt, MAX_RETRIES,
                    )
                    time.sleep(
                        RETRY_SLEEP * attempt
                    )
                    continue

                log.warning(
                    "%s -> %d: %s",
                    path, r.status_code,
                    r.text[:200],
                )
                return None

            except requests.exceptions.Timeout:
                last_err = "timeout"
                log.warning(
                    "%s: timeout (try %d/%d)",
                    path, attempt, MAX_RETRIES,
                )
                time.sleep(RETRY_SLEEP * attempt)
            except requests.exceptions.ConnectionError:
                last_err = "conn"
                log.warning(
                    "%s: conn error (try %d/%d)",
                    path, attempt, MAX_RETRIES,
                )
                time.sleep(RETRY_SLEEP * attempt)
            except Exception as e:
                log.error("%s: %s", path, e)
                return None

        log.error(
            "%s: all retries failed (%s)",
            path, last_err,
        )
        return None

    def public_get(self, path, params=None):
        return self._request(
            "GET", path, params=params,
        )

    def signed_get(self, path, params=None):
        if not self.api_key or not self.api_secret:
            log.error("API keys not set")
            return None

        params = dict(params or {})
        params["timestamp"] = int(
            time.time() * 1000
        )
        params["recvWindow"] = RECV_WINDOW
        params["signature"] = self._sign(params)

        return self._request(
            "GET", path,
            params=params,
            headers=self._headers(),
        )

    def signed_post(self, path, params=None):
        if not self.api_key or not self.api_secret:
            log.error("API keys not set")
            return None

        params = dict(params or {})
        params["timestamp"] = int(
            time.time() * 1000
        )
        params["recvWindow"] = RECV_WINDOW
        params["signature"] = self._sign(params)

        return self._request(
            "POST", path,
            params=params,
            headers=self._headers(),
            use_body=True,
        )

    def signed_delete(self, path, params=None):
        if not self.api_key or not self.api_secret:
            log.error("API keys not set")
            return None

        params = dict(params or {})
        params["timestamp"] = int(
            time.time() * 1000
        )
        params["recvWindow"] = RECV_WINDOW
        params["signature"] = self._sign(params)

        return self._request(
            "DELETE", path,
            params=params,
            headers=self._headers(),
            use_body=True,
        )


def is_configured():
    key = os.getenv("MEXC_API_KEY", "").strip()
    sec = os.getenv("MEXC_API_SECRET", "").strip()
    return bool(key and sec)