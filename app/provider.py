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
    url = f"{BASE_URL}/{endpoint}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers={"X-Auth-Token": token}, params=params or {})
    except httpx.TimeoutException:
        log.error("football-data.org timeout for %s", endpoint)
        raise ProviderError("timeout", "The football data service timed out.")
    except httpx.HTTPError as exc:
        log.error("football-data.org network error for %s: %s", endpoint, type(exc).__name__)
        raise ProviderError("network", "Could not reach the football data service.") from exc

    status = resp.status_code
    if status == 200:
        try:
            data = resp.json()
        except ValueError:
            log.error("football-data.org returned invalid JSON for %s", endpoint)
            raise ProviderError("bad_response", "The football data service returned an unreadable response.")
        if not isinstance(data, dict):
            raise ProviderError("bad_response", "The football data service returned an unexpected response.")
        return data

    log.error("football-data.org error %s for %s: %s", status, endpoint, resp.text[:200])
    if status in (400, 401, 403):
        raise ProviderError("auth", "The football data token was rejected, or your plan does not include this competition.")
    if status == 404:
        raise ProviderError("not_found", "The football data service has no data for this request.")
    if status == 429:
        raise ProviderError("rate_limited", "The football data service rate limit was reached. Try again in a minute.")
    raise ProviderError("upstream", f"The football data service is unavailable (HTTP {status}).")


def _https(url):
    if isinstance(url, str) and url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def _normalize_match(m: dict):
    comp = m.get("competition") or {}
    comp_id = comp.get("id")
    if comp_id not in COMPETITIONS:
        return None
    home_t = m.get("homeTeam") or {}
    away_t = m.get("awayTeam") or {}
    score = m.get("score") or {}
    ft = score.get("fullTime") or {}
    return {
        "fixture": {
            "id": m.get("id"),
            "date": m.get("utcDate"),
            "status": {"short": m.get("status")},
        },
        "teams": {
            "home": {"name": home_t.get("name"), "logo": _https(home_t.get("crest"))},
            "away": {"name": away_t.get("name"), "logo": _https(away_t.get("crest"))},
        },
        "league": {"id": comp_id, "name": comp.get("name")},
        "goals": {"home": ft.get("home"), "away": ft.get("away")},
        "score": score,
    }


def flatten_fixture(item: dict) -> dict:
    """Flat shape shared by service.py and main.py (one place, so they stay in sync)."""
    f = item.get("fixture") or {}
    t = item.get("teams") or {}
    lg = item.get("league") or {}
    goals = item.get("goals") or {}
    home = t.get("home") or {}
    away = t.get("away") or {}
    return {
        "fixture_id": f.get("id"),
        "kickoff": f.get("date"),
        "status": (f.get("status") or {}).get("short"),
        "league_id": lg.get("id"),
        "league": COMPETITIONS.get(lg.get("id"), lg.get("name")),
        "home_team": home.get("name"),
        "home_logo": home.get("logo"),
        "away_team": away.get("name"),
        "away_logo": away.get("logo"),
        "home_score": goals.get("home"),
        "away_score": goals.get("away"),
    }


def no_fixtures_message(day: date) -> str:
    label = day.strftime("%A, %d %B %Y")
    return (
        f"No matches from the supported competitions are scheduled on {label}. "
        "Top leagues often have no games on some weekdays (Mondays especially) or during "
        "international breaks. Try another date."
    )


async def get_fixtures_checked(date_str: str):
    """Returns (fixtures, error). error is None or {"code", "message"}. Never raises."""
    try:
        day = date.fromisoformat(date_str)
    except ValueError:
        return [], {"code": "bad_date", "message": "Invalid date. Use YYYY-MM-DD."}
    try:
        data = await _get("matches", {"dateFrom": day.isoformat(), "dateTo": (day + timedelta(days=1)).isoformat()})
    except ProviderError as exc:
        return [], exc.as_dict()

    raw_matches = data.get("matches")
    if not isinstance(raw_matches, list):
        return [], None

    out = []
    for m in raw_matches:
        try:
            if str(m.get("utcDate", ""))[:10] != day.isoformat():
                continue
            item = _normalize_match(m)
            if item:
                out.append(item)
        except Exception as exc:  # one bad row must not break the day
            log.error("Skipping unreadable match row: %s", exc)
    return out, None


async def get_fixtures(date_str: str) -> list:
    """Backward-compatible: list of fixtures, [] on any failure (already logged)."""
    fixtures, _ = await get_fixtures_checked(date_str)
    return fixtures


async def get_finished(league_id: int, season: int, strict: bool = False) -> list:
    """Finished matches for strength fitting. strict=True raises ProviderError on failure."""
    if league_id not in COMPETITIONS:
        return []
    key = (league_id, season)
    hit = _finished_cache.get(key)
    if hit and time.time() - hit[0] < FINISHED_TTL:
        return hit[1]
    try:
        data = await _get(f"competitions/{league_id}/matches", {"status": "FINISHED", "season": season})
    except ProviderError:
        if strict:
            raise
        return []

    results = []
    for m in data.get("matches") or []:
        try:
            ft = (m.get("score") or {}).get("fullTime") or {}
            hg, ag = ft.get("home"), ft.get("away")
            home = (m.get("homeTeam") or {}).get("name")
            away = (m.get("awayTeam") or {}).get("name")
            if hg is None or ag is None or not home or not away:
                continue
            results.append({
                "date": str(m.get("utcDate", ""))[:10],
                "home": home,
                "away": away,
                "hg": hg,
                "ag": ag,
            })
        except Exception as exc:
            log.error("Skipping unreadable finished match: %s", exc)
    _finished_cache[key] = (time.time(), results)
    return results
