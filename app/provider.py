"""Server-side API-Football client. The API key never leaves the server."""
import asyncio
import time
import httpx
from app.config import settings

BASE_URL = "https://v3.football.api-sports.io"

# API-Football league IDs (confirm coverage on your plan in the provider dashboard).
COMPETITIONS = {39: "Premier League", 140: "La Liga", 135: "Serie A", 78: "Bundesliga", 61: "Ligue 1",
                2: "UEFA Champions League"}
DOMESTIC = {39, 140, 135, 78, 61}   # the goals model is fitted per domestic league

_cache: dict = {}
stats = {"requests": 0}


def _cached(key, ttl):
    hit = _cache.get(key)
    return hit[1] if hit and time.time() - hit[0] < ttl else None


async def _get(path: str, params: dict, retries: int = 3) -> dict:
    if not settings.api_football_key:
        raise RuntimeError("API_FOOTBALL_KEY is not configured.")
    delay = 1.0
    async with httpx.AsyncClient(timeout=25) as client:
        for attempt in range(retries):
            stats["requests"] += 1
            r = await client.get(f"{BASE_URL}{path}", params=params, headers={"x-apisports-key": settings.api_football_key})
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                await asyncio.sleep(delay); delay *= 2
                continue
            r.raise_for_status()
            payload = r.json()
            errs = payload.get("errors")
            if errs:
                raise RuntimeError("Provider error: " + (str(list(errs.values())[0]) if isinstance(errs, dict) else str(errs))[:160])
            return payload
    raise RuntimeError("Provider request failed.")


async def get_fixtures(date: str, timezone: str | None = None) -> list[dict]:
    key = ("fx", date)
    hit = _cached(key, 600)
    if hit is not None:
        return hit
    payload = await _get("/fixtures", {"date": date, "timezone": timezone or settings.display_timezone})
    out = [i for i in payload.get("response", []) if (i.get("league") or {}).get("id") in COMPETITIONS]
    _cache[key] = (time.time(), out)
    return out


async def get_finished(league: int, season: int) -> list[dict]:
    """Finished matches (status FT) for one league-season. Cached 12h: one request per league-season."""
    key = ("hist", league, season)
    hit = _cached(key, 12 * 3600)
    if hit is not None:
        return hit
    payload = await _get("/fixtures", {"league": league, "season": season, "status": "FT"})
    out = []
    for i in payload.get("response", []):
        g, t, f = i.get("goals") or {}, i.get("teams") or {}, i.get("fixture") or {}
        if g.get("home") is None or g.get("away") is None or not f.get("date"):
            continue
        out.append({"date": f["date"][:10], "home": (t.get("home") or {}).get("name"),
                    "away": (t.get("away") or {}).get("name"), "hg": int(g["home"]), "ag": int(g["away"])})
    _cache[key] = (time.time(), out)
    return out


async def get_odds(fixture_id: int) -> list[dict]:
    key = ("odds", fixture_id)
    hit = _cached(key, 1200)
    if hit is not None:
        return hit
    payload = await _get("/odds", {"fixture": fixture_id})
    out = payload.get("response", [])
    _cache[key] = (time.time(), out)
    return out
