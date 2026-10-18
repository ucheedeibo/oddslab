"""Integration with API-Football for live fixtures, historical results, and league data."""
import logging
import os
import httpx
from datetime import date

log = logging.getLogger("oddslab")

API_KEY = os.getenv("API_FOOTBALL_KEY", "")
BASE_URL = "https://v3.football.api-sports.io"

DOMESTIC = {
    39: "Premier League",
    140: "La Liga",
    135: "Serie A",
    78: "Bundesliga",
    61: "Ligue 1",
}

COMPETITIONS = DOMESTIC.copy()

stats = {"requests": 0}


async def _get(endpoint: str, params: dict) -> list:
    if not API_KEY:
        log.warning("API_FOOTBALL_KEY not set.")
        return []

    headers = {"x-apisports-key": API_KEY}
    stats["requests"] += 1

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{BASE_URL}/{endpoint}", headers=headers, params=params)
        if resp.status_code != 200:
            log.error(f"API-Football error {resp.status_code}: {resp.text}")
            return []
        data = resp.json()
        return data.get("response", [])


async def get_fixtures(date_str: str) -> list:
    """Fetches all fixtures for a given date string (YYYY-MM-DD) including goal scores."""
    raw = await _get("fixtures", {"date": date_str})
    formatted = []
    for item in raw:
        formatted.append({
            "fixture": item.get("fixture", {}),
            "teams": item.get("teams", {}),
            "league": item.get("league", {}),
            "goals": item.get("goals", {}),  # Preserves goal scores
            "score": item.get("score", {}),
        })
    return formatted


async def get_finished(league_id: int, season: int) -> list:
    """Fetches finished matches for a given league and season for strength calculations."""
    raw = await _get("fixtures", {"league": league_id, "season": season, "status": "FT"})
    results = []
    for item in raw:
        f = item.get("fixture", {})
        t = item.get("teams", {})
        g = item.get("goals", {})
        if g.get("home") is not None and g.get("away") is not None:
            results.append({
                "date": f.get("date", "")[:10],
                "home": t.get("home", {}).get("name"),
                "away": t.get("away", {}).get("name"),
                "hg": g.get("home"),
                "ag": g.get("away"),
            })
    return results
