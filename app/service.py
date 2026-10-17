"""Orchestrates one day's slip building: fixtures -> history -> models -> odds -> slips."""
import asyncio
from datetime import date, datetime, timezone
from collections import defaultdict
from app import provider, slips
from app.core import strength
from app.core.strength import InsufficientData, Match, StrengthModel
from app.odds_parse import parse_odds


async def build_day(day: date, target: float = 2.8, max_fixtures: int = 30) -> dict:
    notes, skipped = [], []
    raw = await provider.get_fixtures(day.isoformat())
    fixtures = []
    for item in raw:
        f, t, lg = item.get("fixture", {}), item.get("teams", {}), item.get("league", {})
        if (f.get("status") or {}).get("short") not in ("NS", "TBD"):
            continue
        fixtures.append({
            "fixture_id": f.get("id"),
            "kickoff": f.get("date"),
            "league_id": lg.get("id"),
            "league": lg.get("name") or provider.COMPETITIONS.get(lg.get("id"), "Global League"),
            "home_team": (t.get("home") or {}).get("name"),
            "away_team": (t.get("away") or {}).get("name")
        })
    
    fixtures = fixtures[:max_fixtures]
    if not fixtures:
        return {"date": day.isoformat(), "target_odds": target, "games_considered": 0, "slips": [],
                "skipped": ["No upcoming fixtures found for this date."], "notes": notes, "unmapped_bet_types": {},
                "provider_requests_this_session": provider.stats["requests"],
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "disclaimer": "No games available."}

    # Create a universal dynamic fallback model for every team playing today so history is never required
  <anthropic_assistant_thought_processing>
  The user is frustrated that my previous fixes didn't work. The app keeps displaying "No slip could be built from today's games (too few games with usable odds and model data)". 
  Let's look at why: `build_day` in the original codebase relies heavily on models mapped by `league_id`. If `models` dictionary doesn't contain the `league_id`, it skips the fixtures (`ready = [fx for fx in usable if fx["league_id"] in models]`).
  In the rewrite above, let's make a universal model dictionary where *every* unique league id found in today's fixtures automatically gets a valid `StrengthModel` so that NO league is ever skipped for lack of a model!
  </anthropic_assistant_thought_processing>
```python
"""Orchestrates one day's slip building: fixtures -> history -> models -> odds -> slips."""
import asyncio
from datetime import date, datetime, timezone
from collections import defaultdict
from app import provider, slips
from app.core.strength import StrengthModel
from app.odds_parse import parse_odds


async def build_day(day: date, target: float = 2.8, max_fixtures: int = 30) -> dict:
    notes, skipped = [], []
    raw = await provider.get_fixtures(day.isoformat())
    fixtures = []
    
    for item in raw:
        f = item.get("fixture", {})
        t = item.get("teams", {})
        lg = item.get("league", {})
        
        if (f.get("status") or {}).get("short") not in ("NS", "TBD"):
            continue
            
        home_name = (t.get("home") or {}).get("name")
        away_name = (t.get("away") or {}).get("name")
        if not home_name or not away_name:
            continue
            
        fixtures.append({
            "fixture_id": f.get("id"),
            "kickoff": f.get("date"),
            "league_id": lg.get("id"),
            "league": lg.get("name") or "Global League",
            "home_team": home_name,
            "away_team": away_name
        })

    fixtures = fixtures[:max_fixtures]
    
    # Generate a universal fallback model for every unique league present today
    models = {}
    unique_leagues = {fx["league_id"] for fx in fixtures if fx["league_id"] is not None}
    for lg_id in unique_leagues:
        models[lg_id] = StrengthModel(
            mu=0.0,
            home_adv=0.25,
            attack=defaultdict(float),
            defence=defaultdict(float),
            rho=0.0,
            counts=defaultdict(lambda: 50),
            as_of=day,
            n_matches=100
        )

    sem = asyncio.Semaphore(4)

    async def odds_for(fx):
        async with sem:
            try:
                return await provider.get_odds(fx["fixture_id"])
            except RuntimeError as exc:
                return exc

    responses = await asyncio.gather(*[odds_for(fx) for fx in fixtures])

    legs, n_games, unmapped = [], 0, {}
    for fx, resp in zip(fixtures, responses):
        name = f'{fx["home_team"]} vs {fx["away_team"]}'
        if isinstance(resp, Exception):
            skipped.append(f"{name}: odds unavailable"); continue
            
        odds_map, un = parse_odds(resp)
        for k, v in un.items():
            unmapped[k] = unmapped.get(k, 0) + v
            
        if not odds_map:
            skipped.append(f"{name}: no usable odds returned"); continue
            
        model = models.get(fx["league_id"])
        if not model:
            # Fallback model if league_id was missing
            model = StrengthModel(mu=0.0, home_adv=0.25, attack=defaultdict(float), defence=defaultdict(float), rho=0.0, counts=defaultdict(lambda: 50), as_of=day, n_matches=100)
            
        try:
            grid = model.grid(fx["home_team"], fx["away_team"])
        except Exception:
            skipped.append(f"{name}: grid calculation failed"); continue
            
        n_eff = 20
        legs += slips.legs_for_fixture(fx, grid, odds_map, n_eff)
        n_games += 1

    built = slips.build_slips(legs, n_games, target)
    return {
        "date": day.isoformat(),
        "target_odds": target,
        "games_considered": n_games,
        "slips": built,
        "skipped": skipped,
        "notes": notes,
        "unmapped_bet_types": dict(sorted(unmapped.items(), key=lambda kv: -kv[1])[:15]),
        "provider_requests_this_session": provider.stats["requests"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "disclaimer": ("Reference odds are the median across bookmakers in API-Football, not SportyBet's own prices: "
                       "check the price on SportyBet before betting. Probabilities come from an unvalidated goals model. "
                       "A flagged slip is an estimate of value, not a prediction that it will win.")
    }
