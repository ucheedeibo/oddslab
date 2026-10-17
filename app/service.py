"""Orchestrates one day's slip building: fixtures -> history -> models -> odds -> slips."""
import asyncio
from datetime import date, datetime, timezone
from app import provider, slips
from app.core import strength
from app.core.strength import InsufficientData, Match
from app.odds_parse import parse_odds


def season_for(day: date) -> int:
    return day.year if day.month >= 8 else day.year - 1


async def _models(day: date, leagues: set[int], notes: list[str]) -> dict:
    models = {}
    for lg in leagues:
        matches = []
        for season in (season_for(day), season_for(day) - 1):
            try:
                rows = await provider.get_finished(lg, season)
            except RuntimeError as exc:
                notes.append(f"{provider.COMPETITIONS[lg]} {season}: history unavailable ({exc})")
                continue
            matches += [Match(date.fromisoformat(r["date"]), r["home"], r["away"], r["hg"], r["ag"]) for r in rows]
        try:
            models[lg] = strength.fit(matches, day)
        except InsufficientData as exc:
            notes.append(f"{provider.COMPETITIONS[lg]}: no model ({exc})")
    return models


async def build_day(day: date, target: float = 3.0, max_fixtures: int = 30) -> dict:
    notes, skipped = [], []
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
            skipped.append(f'{fx["home_team"]} vs {fx["away_team"]}: {fx["league"]} has no model yet')
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
    responses = await asyncio.gather(*[odds_for(fx) for fx in ready])

    legs, n_games, unmapped = [], 0, {}
    for fx, resp in zip(ready, responses):
        name = f'{fx["home_team"]} vs {fx["away_team"]}'
        if isinstance(resp, Exception):
            skipped.append(f"{name}: odds unavailable ({resp})"); continue
        odds_map, un = parse_odds(resp)
        for k, v in un.items():
            unmapped[k] = unmapped.get(k, 0) + v
        if not odds_map:
            skipped.append(f"{name}: no usable odds returned"); continue
        model = models[fx["league_id"]]
        try:
            grid = model.grid(fx["home_team"], fx["away_team"])
        except InsufficientData as exc:
            skipped.append(f"{name}: {exc}"); continue
        n_eff = min(model.counts[fx["home_team"]], model.counts[fx["away_team"]])
        legs += slips.legs_for_fixture(fx, grid, odds_map, n_eff)
        n_games += 1

    built = slips.build_slips(legs, n_games, target)
    return {"date": day.isoformat(), "target_odds": target, "games_considered": n_games, "slips": built,
            "skipped": skipped, "notes": notes, "unmapped_bet_types": dict(sorted(unmapped.items(), key=lambda kv: -kv[1])[:15]),
            "provider_requests_this_session": provider.stats["requests"],
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "disclaimer": ("Reference odds are the median across bookmakers in API-Football, not SportyBet's own prices: "
                           "check the price on SportyBet before betting. Probabilities come from an unvalidated goals model. "
                           "A flagged slip is an estimate of value, not a prediction that it will win.")}
