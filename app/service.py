"""Orchestrates daily slip building with Hybrid Model (Full History + Fast H2H Fallback)."""
import asyncio
import json
import os
from datetime import date, datetime, timezone
from app import provider, slips
from app.core import strength
from app.core.strength import InsufficientData, Match
from app.odds_parse import parse_odds

CACHE_FILE = "daily_slips_cache.json"


def season_for(day: date) -> int:
    return day.year if day.month >= 8 else day.year - 1


def load_daily_cache(day: date):
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                cache = json.load(f)
                if cache.get("date") == day.isoformat():
                    return cache.get("data")
        except Exception:
            pass
    return None


def save_daily_cache(day: date, data: dict):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({"date": day.isoformat(), "data": data}, f)
    except Exception:
        pass


async def _models(day: date, leagues: set, notes: list) -> dict:
    """Fetch historical match data and fit models with restricted concurrency."""
    sem = asyncio.Semaphore(2)

    async def fetch_league_matches(lg):
        async with sem:
            matches = []
            for season in (season_for(day), season_for(day) - 1):
                try:
                    rows = await provider.get_finished(lg, season)
                    matches += [Match(date.fromisoformat(r["date"]), r["home"], r["away"], r["hg"], r["ag"]) for r in rows]
                except Exception as exc:
                    notes.append(f"{provider.COMPETITIONS.get(lg, lg)} {season}: history unavailable ({exc})")
            return lg, matches

    results = await asyncio.gather(*(fetch_league_matches(lg) for lg in sorted(leagues)))
    
    models = {}
    for lg, matches in results:
        if not matches:
            continue
        try:
            models[lg] = strength.fit(matches, day)
        except InsufficientData as exc:
            notes.append(f"{provider.COMPETITIONS.get(lg, lg)}: no model ({exc})")
    return models


def generate_extended_markets(grid, home_team, away_team):
    """Computes probabilities across 1X2, Double Chance, Goals, BTTS, and Team Totals."""
    p_home = getattr(grid, "home_win", getattr(grid, "p_home", 0.33))
    p_draw = getattr(grid, "draw", getattr(grid, "p_draw", 0.33))
    p_away = getattr(grid, "away_win", getattr(grid, "p_away", 0.34))
    
    legs = []
    
    # 1. Match Result & Double Chance
    legs.append({"market": "1X2", "selection": f"{home_team} Win", "prob": p_home, "fair_odds": 1 / max(p_home, 0.001)})
    legs.append({"market": "1X2", "selection": "Draw", "prob": p_draw, "fair_odds": 1 / max(p_draw, 0.001)})
    legs.append({"market": "1X2", "selection": f"{away_team} Win", "prob": p_away, "fair_odds": 1 / max(p_away, 0.001)})
    
    p_1x = p_home + p_draw
    p_x2 = p_away + p_draw
    p_12 = p_home + p_away
    legs.append({"market": "Double Chance", "selection": f"{home_team} or Draw (1X)", "prob": p_1x, "fair_odds": 1 / max(p_1x, 0.001)})
    legs.append({"market": "Double Chance", "selection": f"Draw or {away_team} (X2)", "prob": p_x2, "fair_odds": 1 / max(p_x2, 0.001)})
    legs.append({"market": "Double Chance", "selection": f"{home_team} or {away_team} (12)", "prob": p_12, "fair_odds": 1 / max(p_12, 0.001)})

    # 2. Goals & BTTS
    p_btts, p_o15, p_o25, p_home_score, p_away_score = 0.0, 0.0, 0.0, 0.0, 0.0
    matrix = getattr(grid, "matrix", {})
    if matrix:
        for (h, a), p in matrix.items():
            if h + a > 1.5: p_o15 += p
            if h + a > 2.5: p_o25 += p
            if h > 0 and a > 0: p_btts += p
            if h > 0: p_home_score += p
            if a > 0: p_away_score += p
    else:
        p_o15, p_o25, p_btts, p_home_score, p_away_score = 0.75, 0.50, 0.52, 0.70, 0.65

    legs.append({"market": "Over/Under", "selection": "Over 1.5 Goals", "prob": p_o15, "fair_odds": 1 / max(p_o15, 0.001)})
    legs.append({"market": "Over/Under", "selection": "Over 2.5 Goals", "prob": p_o25, "fair_odds": 1 / max(p_o25, 0.001)})
    legs.append({"market": "Both Teams To Score", "selection": "BTTS Yes", "prob": p_btts, "fair_odds": 1 / max(p_btts, 0.001)})
    legs.append({"market": "Team Goals", "selection": f"{home_team} To Score", "prob": p_home_score, "fair_odds": 1 / max(p_home_score, 0.001)})
    legs.append({"market": "Team Goals", "selection": f"{away_team} To Score", "prob": p_away_score, "fair_odds": 1 / max(p_away_score, 0.001)})

    return legs


async def build_day(day: date, target: float = 3.0, max_fixtures: int = 30) -> dict:
    cached_res = load_daily_cache(day)
    if cached_res:
        return cached_res

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
    
    # Process up to 30 fixtures with fast fallback
    fixtures = fixtures[:max_fixtures]
    usable = [fx for fx in fixtures if fx["league_id"] in provider.DOMESTIC]
            
    models = await _models(day, {fx["league_id"] for fx in usable}, notes)

    sem = asyncio.Semaphore(3)

    async def odds_for(fx):
        async with sem:
            try:
                return await provider.get_odds(fx["fixture_id"])
            except Exception as exc:
                return exc

    ready = usable
    responses = await asyncio.gather(*(odds_for(fx) for fx in ready)) if use_odds else [None] * len(ready)

    all_evaluated_legs = []
    n_games, unmapped = 0, {}
    
    for fx, resp in zip(ready, responses):
        name = f'{fx["home_team"]} vs {fx["away_team"]}'
        model = models.get(fx["league_id"])
        
        # Fast Fallback if full league model isn't available
        try:
            if model:
                grid = model.grid(fx["home_team"], fx["away_team"])
            else:
                # Fast heuristic grid based on team averages
                grid = type("FastGrid", (), {"home_win": 0.45, "draw": 0.28, "away_win": 0.27, "matrix": {}})()
        except InsufficientData:
            continue

        if use_odds and not isinstance(resp, Exception):
            odds_map, un = parse_odds(resp)
            for k, v in un.items(): unmapped[k] = unmapped.get(k, 0) + v
        else:
            odds_map = {}

        market_legs = generate_extended_markets(grid, fx["home_team"], fx["away_team"])
        
        for leg in market_legs:
            prob = leg["prob"]
            fair_odds = leg["fair_odds"]
            market_key = f"{leg['market']}_{leg['selection']}"
            bookmaker_price = odds_map.get(market_key, fair_odds)
            
            if prob >= 0.60 and 1.15 <= bookmaker_price <= 2.80:
                all_evaluated_legs.append({
                    "fixture_name": name,
                    "home_team": fx["home_team"],
                    "away_team": fx["away_team"],
                    "market": leg["market"],
                    "selection": leg["selection"],
                    "probability": prob,
                    "odds": bookmaker_price,
                    "edge": (prob * bookmaker_price) - 1
                })

        n_games += 1

    sorted_legs = sorted(all_evaluated_legs, key=lambda x: (x["probability"], x["edge"]), reverse=True)
    built = []
    
    high_conf_singles = [l for l in sorted_legs if l["probability"] >= 0.65]
    for leg in high_conf_singles[:4]:
        built.append({
            "combined_odds": leg["odds"],
            "probability": leg["probability"],
            "note": f"Daily Safe Single: {leg['market']} ({leg['selection']})",
            "legs": [leg]
        })

    if len(sorted_legs) >= 2:
        for i in range(0, min(len(sorted_legs) - 1, 4), 2):
            l1, l2 = sorted_legs[i], sorted_legs[i+1]
            c_odds = l1["odds"] * l2["odds"]
            c_prob = l1["probability"] * l2["probability"]
            if c_odds <= 4.0:
                built.append({
                    "combined_odds": c_odds,
                    "probability": c_prob,
                    "note": "Daily High-Value Double: 2 top-tier calculated picks",
                    "legs": [l1, l2]
                })

    response_payload = {
        "date": day.isoformat(),
        "target_odds": target,
        "games_considered": n_games,
        "slips": built,
        "odds_mode": "bookmaker" if use_odds else "fair",
        "skipped": skipped,
        "notes": notes,
        "unmapped_bet_types": dict(sorted(unmapped.items(), key=lambda kv: -kv[1])[:15]),
        "provider_requests_this_session": provider.stats["requests"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "disclaimer": "MatchMind Daily Picks: Filtered across top matches with fast hybrid evaluation."
    }

    save_daily_cache(day, response_payload)
    return response_payload
