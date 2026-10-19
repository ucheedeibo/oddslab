"""football-data.org (free tier) integration: fixtures, finished matches and team crests.

Design rules:
- A missing/invalid token, rate limit or network failure never crashes the app.
  Failures are logged (never the token) and reported to callers as ProviderError
  or, for the *_checked helpers, as a small {"code", "message"} dict.
- Free tier allows ~10 requests/minute, so calls are throttled and finished-match
  history is cached in memory.
"""
import asyncio
import logging
import os
import time
from datetime import date, timedelta

import httpx

log = logging.getLogger("oddslab")

BASE_URL = "https://api.football-data.org/v4"

# The 12 competitions included in the football-data.org free tier.
DOMESTIC = {
    2021: "Premier League",
    2016: "Championship",
    2014: "La Liga",
    2019: "Serie A",
    2002: "Bundesliga",
    2015: "Ligue 1",
    2003: "Eredivisie",
    2017: "Primeira Liga",
    2013: "Brasileirão Série A",
    2001: "UEFA Champions League",
    2018: "European Championship",
    2000: "FIFA World Cup",
}
COMPETITIONS = DOMESTIC.copy()

# Tournaments have no season-long strength history, so no model is fitted for them.
NO_MODEL = {2018, 2000}
MODELLED = {cid: name for cid, name in COMPETITIONS.items() if cid not in NO_MODEL}

stats = {"requests": 0}

# Free tier: 10 calls/minute. Stay just under it.
MAX_PER_MINUTE = 9
MAX_WAIT_SECONDS = 45
FINISHED_TTL = 6 * 3600

_calls: list = []
_throttle_lock = asyncio.Lock()
_finished_cache: dict = {}


class ProviderError(RuntimeError):
    """Raised when football-data.org cannot give us usable data."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


def _token() -> str:
    return (os.getenv("FOOTBALL_DATA_TOKEN") or os.getenv("FOOTBALL_DATA_KEY") or "").strip()


async def _throttle() -> None:
    async with _throttle_lock:
        now = time.monotonic()
        while _calls and now - _calls[0] >= 60:
            _calls.pop(0)
        if len(_calls) >= MAX_PER_MINUTE:
            wait = 60 - (now - _calls[0]) + 0.1
            if wait > MAX_WAIT_SECONDS:
                raise ProviderError("rate_limited", "Too many data requests this minute. Try again shortly.")
            await asyncio.sleep(wait)
        _calls.append(time.monotonic())


async def _get(endpoint: str, params: dict = None) -> dict:
    """GET from football-data.org. Returns parsed JSON or raises ProviderError."""
    token = _token()
    if not token:
        log.error("FOOTBALL_DATA_TOKEN is not set; cannot call football-data.org")
        raise ProviderError("missing_token", "The football data token is not configured on the server.")

    await _throttle()
    stats["requests"] += 1
    url = f"{BASE_URL}/{endpoint
