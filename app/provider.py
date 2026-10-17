"""Server-side API-Football client. The API key never leaves the server."""
import httpx
from app.config import settings

BASE_URL = "https://v3.football.api-sports.io"

# API-Football league IDs (confirm coverage on your plan in the provider dashboard).
COMPETITIONS = {39: "Premier League", 140: "La Liga", 135: "Serie A", 78: "Bundesliga", 61: "Ligue 1",
                2: "UEFA Champions League"}


async def get_fixtures(date: str, timezone: str | None = None) -> list[dict]:
    if not settings.api_football_key:
        raise RuntimeError("API_FOOTBALL_KEY is not configured.")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{BASE_URL}/fixtures",
                             params={"date": date, "timezone": timezone or settings.display_timezone},
                             headers={"x-apisports-key": settings.api_football_key})
        r.raise_for_status()
        payload = r.json()
    if payload.get("errors"):
        raise RuntimeError("Football data provider returned an API error (check plan/limits).")
    return [i for i in payload.get("response", []) if (i.get("league") or {}).get("id") in COMPETITIONS]
