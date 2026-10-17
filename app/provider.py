"""Server-side football-data.org client (free plan: 10 requests/minute, 12 competitions).
The token never leaves the server. Results are reshaped into the layout the rest of the app already uses."""
import asyncio
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import httpx
from app.config import settings

BASE = "https://api.football-data.org/v4"
COMPETITIONS = {"PL": "Premier League", "PD": "La Liga", "SA": "Serie A", "BL1": "Bundesliga",
                "FL1": "Ligue 1", "CL": "UEFA Champions League"}
DOMESTIC = {"PL", "PD", "SA", "BL1", "FL1"}   # the goals model is fitted per domestic league
_STATUS = {"SCHEDULED": "NS", "TIMED": "NS", "IN_PLAY": "LIVE", "PAUSED": "LIVE", "FINISHED": "FT",
           "POSTPONED": "PST", "SUSPENDED": "SUSP", "CANCELLED": "CANC", "AWARDED": "AWD"}

_cache: dict = {}
_sent: list = []          # timestamps of recent requests (free plan allows 10 per minute)
_lock = asyncio.Lock()
stats = {"requests": 0}


def has_odds() -> bool:
    """football-data.org's free plan has no betting odds, so no odds feed is connected yet."""
    return False


async def get_odds(fixture_id):
    raise RuntimeError("No odds source is connected.")


def _cached(key, ttl):
    hit = _cache.get(key)
    return hit[1] if hit and time.time() - hit[0] < ttl else None


async def _throttle():
    async with _lock:
        now = time.time()
        while _sent and now - _sent[0] >= 60:
            _sent.pop(0)
        if len(_sent) >= 9:   # stay under 10 per minute
            await asyncio.sleep(60 - (now - _sent[0]) + 0.5)
            now = time.time()
            while _sent and now - _sent[0] >= 60:
                _sent.pop(0)
        _sent.append(time.time())


async def _get(path: str, params: dict, retries: int = 3) -> dict:
    token = settings.football_data_token
    if not token:
        raise RuntimeError("FOOTBALL_DATA_TOKEN is not configured.")
    delay = 2.0
    async with httpx.AsyncClient(timeout=25) as client:
        for attempt in range(retries):
            await _throttle()
            stats["requests"] += 1
            try:
                r = await client.get(BASE + path, params=params, headers={"X-Auth-Token": token})
            except httpx.TransportError:
                raise RuntimeError("Could not reach football-data.org.")
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                await asyncio.sleep(delay); delay *= 2
                continue
            if r.status_code in (401, 403):
                raise RuntimeError("football-data.org rejected the token, or this data is not in your plan.")
            if r.status_code >= 400:
                raise RuntimeError(f"football-data.org returned HTTP {r.status_code}.")
            return r.json()
    raise RuntimeError("Provider request failed.")


async def get_fixtures(day: str, timezone: str | None = None) -> list[dict]:
    """Matches on a local calendar day, reshaped to {fixture, teams, league}."""
    key = ("fx", day)
    hit = _cached(key, 600)
    if hit is not None:
        return hit
    d = date.fromisoformat(day)
    tz = ZoneInfo(timezone or settings.display_timezone)
    payload = await _get("/matches", {"dateFrom": (d - timedelta(days=1)).isoformat(),
                                      "dateTo": (d + timedelta(days=1)).isoformat(),
                                      "competitions": ",".join(COMPETITIONS)})
    out = []
    for m in payload.get("matches", []):
        code = (m.get("competition") or {}).get("code")
        utc = m.get("utcDate")
        if code not in COMPETITIONS or not utc:
            continue
        if datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(tz).date() != d:
            continue
        out.append({"fixture": {"id": m.get("id"), "date": utc,
                                "status": {"short": _STATUS.get(m.get("status"), m.get("status"))}},
                    "teams": {"home": {"name": (m.get("homeTeam") or {}).get("name")},
                              "away": {"name": (m.get("awayTeam") or {}).get("name")}},
                    "league": {"id": code, "name": COMPETITIONS[code]}})
    _cache[key] = (time.time(), out)
    return out


async def get_finished(league: str, season: int) -> list[dict]:
    """Finished matches for one league-season (one request, cached 12h)."""
    key = ("hist", league, season)
    hit = _cached(key, 12 * 3600)
    if hit is not None:
        return hit
    payload = await _get(f"/competitions/{league}/matches", {"season": season, "status": "FINISHED"})
    out = []
    for m in payload.get("matches", []):
        ft = (m.get("score") or {}).get("fullTime") or {}
        if ft.get("home") is None or ft.get("away") is None or not m.get("utcDate"):
            continue
        out.append({"date": m["utcDate"][:10], "home": (m.get("homeTeam") or {}).get("name"),
                    "away": (m.get("awayTeam") or {}).get("name"), "hg": int(ft["home"]), "ag": int(ft["away"])})
    _cache[key] = (time.time(), out)
    return out
