"""Integration with football-data.org for live fixtures, historical results, and league data."""
import logging
import os
import httpx

log = logging.getLogger("oddslab")

# Reads your football-data.org token from Render
TOKEN = os.getenv("FOOTBALL_DATA_TOKEN", "") or os.getenv("FOOTBALL_DATA_KEY", "")
BASE_URL = "https://api.football-data.org/v4"

DOMESTIC = {
    2021: "Premier League",
    2014: "La Liga",
    2019: "Serie A",
    2002: "Bundesliga",
    2015: "Ligue 1",
}

COMPETITIONS = DOMESTIC.copy()
stats = {"requests": 0}


async def _get(endpoint: str, params: dict = None) -> dict:
    if not TOKEN:
        log.error("CRITICAL: FOOTBALL_DATA_TOKEN environment variable is missing on Render!")
        return {}

    headers = {"X-Auth-Token": TOKEN}
    stats["requests"] += 1

    async with httpx.AsyncClient(timeout=15.0) as client:
        url = f"{BASE_URL}/{endpoint}"
        resp = await client.get(url, headers=headers, params=params or {})
        log.info(f"Football-Data Response Status: {resp.status_code} for {endpoint}")
        
        if resp.status_code != 200:
            log.error(f"Football-Data error {resp.status_code}: {resp.text}")
            return {}
            
        return resp.json()


async def get_fixtures(date_str: str) -> list:
    """Fetches all fixtures for a given date string (YYYY-MM-DD) from football-data.org."""
    data = await _get("matches", {"date": date_str})
    raw_matches = data.get("matches", [])
    
    formatted = []
    for m in raw_matches:
        formatted.append({
            "fixture": {
                "id": m.get("id"),
                "date": m.get("utcDate"),
                "status": {"short": m.get("status")},
            },
            "teams": {
                "home": {"name": m.get("homeTeam", {}).get("name")},
                "away": {"name": m.get("awayTeam", {}).get("name")},
            },
            "league": {
                "id": m.get("competition", {}).get("id"),
                "name": m.get("competition", {}).get("name"),
            },
            "goals": {
                "home": m.get("score", {}).get("fullTime", {}).get("home"),
                "away": m.get("score", {}).get("fullTime", {}).get("away"),
            },
            "score": m.get("score", {}),
        })
    return formatted


async def get_finished(league_id: int, season: int) -> list:
    """Fetches finished matches for strength calculations."""
    data = await _get(f"competitions/{league_id}/matches", {"status": "FINISHED", "season": season})
    raw_matches = data.get("matches", [])
    
    results = []
    for m in raw_matches:
        score = m.get("score", {}).get("fullTime", {})
        hg = score.get("home")
        ag = score.get("away")
        if hg is not None and ag is not None:
            results.append({
                "date": m.get("utcDate", "")[:10],
                "home": m.get("homeTeam", {}).get("name"),
                "away": m.get("awayTeam", {}).get("name"),
                "hg": hg,
                "ag": ag,
            })
    return results
