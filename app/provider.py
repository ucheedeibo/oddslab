"""Integration with football APIs for live fixtures, historical results, and league data."""
import logging
import os
import httpx

log = logging.getLogger("oddslab")

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "") or os.getenv("FOOTBALL_API_KEY", "")
FOOTBALL_DATA_TOKEN = os.getenv("FOOTBALL_DATA_TOKEN", "") or os.getenv("FOOTBALL_DATA_KEY", "")

# Determine active provider mode
USING_API_FOOTBALL = bool(API_FOOTBALL_KEY)

DOMESTIC = {
    39: "Premier League",
    140: "La Liga",
    135: "Serie A",
    78: "Bundesliga",
    61: "Ligue 1",
} if USING_API_FOOTBALL else {
    2021: "Premier League",
    2014: "La Liga",
    2019: "Serie A",
    2002: "Bundesliga",
    2015: "Ligue 1",
}

COMPETITIONS = DOMESTIC.copy()
stats = {"requests": 0}


async def _get(endpoint: str, params: dict = None) -> dict:
    stats["requests"] += 1
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            if USING_API_FOOTBALL:
                url = f"https://v3.football.api-sports.io/{endpoint}"
                headers = {"x-apisports-key": API_FOOTBALL_KEY}
                resp = await client.get(url, headers=headers, params=params or {})
            else:
                url = f"https://api.football-data.org/v4/{endpoint}"
                headers = {"X-Auth-Token": FOOTBALL_DATA_TOKEN}
                resp = await client.get(url, headers=headers, params=params or {})

            log.info(f"API Response Status: {resp.status_code} for {endpoint}")
            if resp.status_code != 200:
                log.error(f"API error {resp.status_code}: {resp.text}")
                return {}
            return resp.json()
        except Exception as e:
            log.error(f"Request exception: {e}")
            return {}


async def get_fixtures(date_str: str) -> list:
    """Fetches all fixtures for a given date string (YYYY-MM-DD)."""
    if USING_API_FOOTBALL:
        data = await _get("fixtures", {"date": date_str})
        raw = data.get("response", []) if isinstance(data, dict) else []
        formatted = []
        for item in raw:
            formatted.append({
                "fixture": item.get("fixture", {}),
                "teams": item.get("teams", {}),
                "league": item.get("league", {}),
                "goals": item.get("goals", {}),
                "score": item.get("score", {}),
            })
        return formatted
    else:
        data = await _get("matches", {"date": date_str})
        raw_matches = data.get("matches", []) if isinstance(data, dict) else []
        formatted = []
        for m in raw_matches:
            comp = m.get("competition") or {}
            home_t = m.get("homeTeam") or {}
            away_t = m.get("awayTeam") or {}
            score = m.get("score") or {}
            ft = score.get("fullTime") or {}
            formatted.append({
                "fixture": {
                    "id": m.get("id"),
                    "date": m.get("utcDate"),
                    "status": {"short": m.get("status")},
                },
                "teams": {
                    "home": {"name": home_t.get("name")},
                    "away": {"name": away_t.get("name")},
                },
                "league": {
                    "id": comp.get("id"),
                    "name": comp.get("name"),
                },
                "goals": {
                    "home": ft.get("home"),
                    "away": ft.get("away"),
                },
                "score": score,
            })
        return formatted


async def get_finished(league_id: int, season: int) -> list:
    """Fetches finished matches for strength calculations."""
    if USING_API_FOOTBALL:
        data = await _get("fixtures", {"league": league_id, "season": season, "status": "FT"})
        raw = data.get("response", []) if isinstance(data, dict) else []
        results = []
        for item in raw:
            f = item.get("fixture", {})
            t = item.get("teams", {})
            g = item.get("goals", {})
            if g.get("home") is not None and g.get("away") is not None:
                results.append({
                    "date": str(f.get("date", ""))[:10],
                    "home": t.get("home", {}).get("name"),
                    "away": t.get("away", {}).get("name"),
                    "hg": g.get("home"),
                    "ag": g.get("away"),
                })
        return results
    else:
        data = await _get(f"competitions/{league_id}/matches", {"status": "FINISHED", "season": season})
        raw_matches = data.get("matches", []) if isinstance(data, dict) else []
        results = []
        for m in raw_matches:
            home_t = m.get("homeTeam") or {}
            away_t = m.get("awayTeam") or {}
            score = (m.get("score") or {}).get("fullTime") or {}
            hg = score.get("home")
            ag = score.get("away")
            if hg is not None and ag is not None:
                results.append({
                    "date": str(m.get("utcDate", ""))[:10],
                    "home": home_t.get("name"),
                    "away": home_t.get("name") if not home_t else home_t.get("name"),
                    "hg": hg,
                    "ag": ag,
                })
        return results
