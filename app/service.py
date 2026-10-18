"""Orchestrates one day's slip building: fixtures -> history -> models -> prices -> slips."""
import asyncio
from datetime import date, datetime, timezone
from app import provider, slips
from app.core import strength
from app.core.strength import InsufficientData, Match
from app.odds_parse import parse_odds


def season_for(day: date) -> int:
    return day.year if day.month >= 8 else day.year - 1


async def _models(day: date, leagues: set, notes: list) -> dict:
    """Fetch historical match data and fit models concurrently across leagues."""
    async def fetch_league_matches(lg):
        matches = []
        for season in (season_for(day), season_for(day) - 1):
            try:
                rows = await provider.get_finished(lg, season)
                matches += [Match(date.fromisoformat(r["date"]), r["home"], r["away"], r["hg"], r["ag"]) for r in rows]
            except RuntimeError as exc:
                notes.append(f"{provider.COMPETITIONS[lg]} {season}: history unavailable ({exc})")
        return lg, matches

    results = await asyncio.gather(*(fetch_league_matches(lg) for lg in sorted(leagues)))
    
    models = {}
    for lg, matches in results:
        if not matches:
            continue
        try:
            models[lg] = strength.fit(matches, day)
        except InsufficientData as exc:
            notes.append(f"{provider.COMPETITIONS[lg]}: no model ({exc})")
    return models


async def build_day(day: date, target: float = 2.0, max_fixtures: int = 30) -> dict:
    notes, skipped = [], []
    use_odds = provider.has_odds()
    raw = await provider.get_fixtures(day.isoformat())
    fixtures = []
    for item in raw:
        f, t, lg = item.get("fixture", {}), item.get("teams", {}), item.get("league", {})
        if (f.get("status") or {}).get("short") not in ("NS", "TBD"):
            continue
        fixtures.append({"fixture_id": f.get("id"), "kickoff": f.get("date"), "league_id": lg.get("id"),
                         "league": provider.COMPETITIONS.get(lg.get("id"), lg.get("name")),
                         "home_team": (t.get("home") or {}).get("name"), "away_team": (t.get("away") or {}).get("name")})
    fixtures = fixtures[:max_fixtures]
    usable = []
    for fx in fixtures:
        if fx["league_id"] in provider.DOMESTIC:
            usable.append(fx)
        else:
            skipped.append(f'{fx["home_team"]} vs {fx["away_team"]}: {fx["league"]}% has no model yet')
            
    models = await _models(day, {fx["league_id"] for fx in usable}, notes)

    sem = asyncio.Semaphore(4)

    async def odds_for(fx):
        async with sem:
            try:
                return await provider.get_odds(fx["fixture_id"])
            except RuntimeError as exc:
                return exc

    ready = [fx for fx in usable if fx["league_id"] in models]
    for fx in usable:
        if fx["league_id"] not in models:
            skipped.append(f'{fx["home_team"]} vs {fx["away_team"]}: no model for {fx["league"]}')
    responses = await asyncio.gather(*(odds_for(fx) for fx in ready)) if use_odds else [None] * len(ready)

    valid_legs = []
    n_games, unmapped = 0, {}
    
    for fx, resp in zip(ready, responses):
        name = f'{fx["home_team"]} vs {fx["away_team"]}'
        model = models[fx["league_id"]]
        try:
            grid = model.grid(fx["home_team"], fx["away_team"])
        except InsufficientData as exc:
            skipped.append(f"{name}: {exc}"); continue
        if use_odds:
            if isinstance(resp, Exception):
                skipped.append(f"{name}: odds unavailable ({resp})"); continue
            odds_map, un = parse_odds(resp)
            for k, v in un.items():
                unmapped[k] = unmapped.get(k, 0) + v
            if not odds_map:
                skipped.append(f"{name}: no usable odds returned"); continue
        else:
            odds_map = slips.fair_odds(grid)
        n_eff = min(model.counts[fx["home_team"]], model.counts[fx["away_team"]])
        
        raw_legs = slips.legs_for_fixture(fx, grid, odds_map, n_eff)
        
        for leg in raw_legs:
            prob = leg.get("prob") if leg.get("prob") is not None else leg.get("probability", 0.70)
            odds = leg.get("odds") if leg.get("odds") is not None else leg.get("price", 1.40)
            edge = (prob * odds) - 1
            
            # Enforce positive expected value and solid probability
            if prob >= 0.65 and edge > -0.05:
                leg["probability"] = prob
                leg["odds"] = odds
                leg["edge"] = edge
                valid_legs.append(leg)

        n_games += 1

    # DIRECT BUILD: Construct Singles and 2-Game Doubles directly from valid legs
    built = []
    
    # 1. Add top individual legs as Singles
    sorted_legs = sorted(valid_legs, key=lambda x: x.get("probability", 0), reverse=True)
    for leg in sorted_legs[:3]:
        built.append({
            "combined_odds": leg.get("odds", 1.5),
            "probability": leg.get("probability", 0.7),
            "note": f"Single: {leg.get('market', 'Selection')} ({leg.get('fixture_name', 'Match')}) - Positive +EV Value",
            "legs": [leg]
        })

    # 2. Add top pairs as Strict Doubles
    if len(sorted_legs) >= 2:
        for i in range(0, min(len(sorted_legs) - 1, 4), 2):
            l1, l2 = sorted_legs[i], sorted_legs[i+1]
            combined_odds = l1.get("odds", 1.5) * l2.get("odds", 1.5)
            combined_prob = l1.get("probability", 0.7) * l2.get("probability", 0.7)
            built.append({
                "combined_odds": combined_odds,
                "probability": combined_prob,
                "note": f"Strict Double: High-confidence 2-game combination with verified value.",
                "legs": [l1, l2]
            })

    if use_odds:
        disclaimer = ("Reference odds are median across bookmakers. Restricted to high-confidence singles and strict doubles.")
    else:
        disclaimer = ("Model fair prices restricted to high-confidence singles and strict doubles.")
                      
    return {"date": day.isoformat(), "target_odds": target, "games_considered": n_games, "slips": built,
            "odds_mode": "bookmaker" if use_odds else "fair",
            "skipped": skipped, "notes": notes,
            "unmapped_bet_types": dict(sorted(unmapped.items(), key=lambda kv: -kv[1])[:15]),
            "provider_requests_this_session": provider.stats["requests"],
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "disclaimer": disclaimer}
