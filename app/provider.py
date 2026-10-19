"""Integration with football-data.org free tier for fixtures, results, and logos."""
import logging
import os
import httpx

log = logging.getLogger("oddslab")

TOKEN = os.getenv("FOOTBALL_DATA_TOKEN", "") or os.getenv("FOOTBALL_DATA_KEY", "")
BASE_URL = "https://api.football-data.org/v4"

# Official 12 Free-Tier Competitions on football-data.org
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
stats = {"requests": 0}


async def _get(endpoint: str, params: dict = None) -> dict:
    if not TOKEN:
        log.error("CRITICAL: FOOTBALL_DATA_TOKEN environment variable is missing on Render!")
        return {}

    headers = {"X-Auth-Token": TOKEN}
    stats["requests"] += 1

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            url = f"{BASE_URL}/{endpoint}"
            resp = await client.get(url, headers=headers, params=params or {})
            log.info(f"Football-Data Response Status: {resp.status_code} for {endpoint}")
            
            if resp.status_code != 200:
                log.error(f"Football-Data error {resp.status_code}: {resp.text}")
                return {}
                
            return resp.json()
        except Exception as e:
            log.error(f"Request exception: {e}")
            return {}


async def get_fixtures(date_str: str) -> list:
    """Fetches all fixtures for a given date string (YYYY-MM-DD) including team logos."""
    data = await _get("matches", {"date": date_str})
    raw_matches = data.get("matches", [])
    if not isinstance(raw_matches, list):
        return []
    
    formatted = []
    for m in raw_matches:
        try:
            comp = m.get("competition") or {}
            comp_id = comp.get("id")
            
            # Only include supported free-tier competitions
            if comp_id not in COMPETITIONS:
                continue

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
                    "home": {
                        "name": home_t.get("name"),
                        "logo": home_t.get("crest"),
                    },
                    "away": {
                        "name": away_t.get("name"),
                        "logo": away_t.get("crest"),
                    },
                },
                "league": {
                    "id": comp_id,
                    "name": comp.get("name"),
                },
                "goals": {
                    "home": ft.get("home"),
                    "away": ft.get("away"),
                },
                "score": score,
            })
        except Exception as parse_err:
            log.error(f"Error parsing match item: {parse_err}")
            continue
            
    return formatted


async def get_finished(league_id: int, season: int) -> list:
    """Fetches finished matches for strength calculations."""
    if league_id not in COMPETITIONS:
        return []
        
    data = await _get(f"competitions/{league_id}/matches", {"status": "FINISHED", "season": season})
    raw_matches = data.get("matches", [])
    if not isinstance(raw_matches, list):
        return []
    
    results = []
    for m in raw_matches:
        try:
            home_t = m.get("homeTeam") or {}
            away_t = m.get("awayTeam") or {}
            score = (m.get("score") or {}).get("fullTime") or {}
            hg = score.get("home")
            ag = score.get("away")
            
            if hg is not None and ag is not None:
                results.append({
                    "date": str(m.get("utcDate", ""))[:10],
                    "home": home_t.get("name"),
                    "away": away_t.get("name"),
                    "hg": hg,
                    "ag": ag,
                })
        except Exception as parse_err:
            log.error(f"Error parsing finished match: {parse_err}")
            continue
            
    return results
