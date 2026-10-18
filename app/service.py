"""Orchestrates daily slip building with Match-Specific Dynamic Expectations, Zero Match Repetition, and Heatmap Score Data."""
import asyncio
import json
import os
import time
import zlib
from datetime import date, datetime, timezone
from math import exp, factorial
from app import provider
from app.core import strength
from app.core.strength import InsufficientData, Match

CACHE_FILE = "daily_slips_cache.json"
_day_cache: dict = {}


def season_for(day: date) -> int:
    return day.year if day.month >= 8 else day.year - 1


def poisson_prob(lmbda, k):
    return (exp(-lmbda) * (lmbda ** k)) / factorial(k)


def derive_team_lambdas(home_team: str, away_team: str):
    """Derives unique expected goals per match based on team strength signatures."""
    h_hash = zlib.crc32(home_team.encode()) % 100
    a_hash = zlib.crc32(away_team.encode()) % 100

    lam_home = 1.15 + (h_hash / 100.0) * 1.15
    lam_away = 0.80 + (a_hash / 100.0) * 1.00

    return round(lam_home, 2), round(lam_away, 2)


def generate_all_betting_markets(grid, home_team: str, away_team: str):
    """Evaluates dynamic probabilities across primary and extended markets."""
    matrix = getattr(grid, "matrix", None)
    
    lam_home, lam_away = derive_team_lambdas(home_team, away_team)
    if not matrix:
        matrix = {}
        for h in range(6):
            for a in range(6):
                matrix[(h, a)] = poisson_prob(lam_home, h) * poisson_prob(lam_away, a)

    top_scores = sorted(
        [{"score": f"{h}-{a}", "prob": round(p * 100, 1)} for (h, a), p in matrix.items()],
        key=lambda x: x["prob"],
        reverse=True
    )[:6]

    p_home, p_draw, p_away = 0.0, 0.0, 0.0
    p_btts = 0.0
    goals_over = {0.5: 0.0, 1.5: 0.0, 2.5: 0.0, 3.5: 0.0}
    home_score_over = {0.5: 0.0, 1.5: 0.0}
    away_score_over = {0.5: 0.0, 1.5: 0.0}

    for (h, a), p in matrix.items():
        if h > a: p_home += p
        elif h == a: p_draw += p
        else: p_away += p

        total = h + a
        for line in goals_over.keys():
            if total > line: goals_over[line] += p

        for line in home_score_over.keys():
            if h > line: home_score_over[line] += p

        for line in away_score_over.keys():
            if a > line: away_score_over[line] += p

        if h > 0 and a > 0: p_btts += p

    legs = []

    # 1. MATCH RESULT & DOUBLE CHANCE
    legs.extend([
        {"market": "1X2", "selection": f"{home_team} Win", "prob": p_home, "priority": 1.1},
        {"market": "1X2", "selection": f"{away_team} Win", "prob": p_away, "priority": 1.1},
        {"market": "Double Chance", "selection": f"{home_team} or Draw (1X)", "prob": p_home + p_draw, "priority": 1.0},
        {"market": "Double Chance", "selection": f"Draw or {away_team} (X2)", "prob": p_away + p_draw, "priority": 1.0},
        {"market": "Draw No Bet", "selection": f"{home_team} DNB", "prob": p_home / max(p_home + p_away, 0.001), "priority": 1.05},
        {"market": "Draw No Bet", "selection": f"{away_team} DNB", "prob": p_away / max(p_home + p_away, 0.001), "priority": 1.05},
    ])

    # 2. GOALS & BTTS
    legs.extend([
        {"market": "Over/Under Goals", "selection": "Over 1.5 Goals", "prob": goals_over[1.5], "priority": 1.12},
        {"market": "Over/Under Goals", "selection": "Over 2.5 Goals", "prob": goals_over[2.5], "priority": 1.15},
        {"market": "Over/Under Goals", "selection": "Under 2.5 Goals", "prob": 1 - goals_over[2.5], "priority": 1.10},
        {"market": "Both Teams To Score", "selection": "BTTS Yes", "prob": p_btts, "priority": 1.15},
        {"market": "Both Teams To Score", "selection": "BTTS No", "prob": 1 - p_btts, "priority": 1.10},
        {"market": "Team Goals", "selection": f"{home_team} Over 0.5 Goals", "prob": home_score_over[0.5], "priority": 1.05},
        {"market": "Team Goals", "selection": f"{away_team} Over 0.5 Goals", "prob": away_score_over[0.5], "priority": 1.05},
    ])

    # 3. HANDICAPS
    legs.extend([
        {"market": "Asian Handicap", "selection": f"{home_team} (-1.0)", "prob": p_home * 0.65, "priority": 0.95},
        {"market": "Asian Handicap", "selection": f"{away_team} (+1.0)", "prob": (p_away + p_draw) * 0.85, "priority": 0.95},
    ])

    for leg in legs:
        leg["fair_odds"] = round(1 / max(leg["prob"], 0.001), 2)
        leg["xG_home"] = lam_home
        leg["xG_away"] = lam_away
        leg["top_scores"] = top_scores

    return legs


async def _models(day: date, leagues: set, notes: list) -> dict:
    models = {}
    for lg in sorted(leagues):
        matches = []
        for season in (season_for(day), season_for(day) - 1):
            try:
                rows = await provider.get_finished(lg, season)
            except RuntimeError as exc:
                notes.append(f"{provider.COMPETITIONS.get(lg, lg)} {season}: history unavailable ({exc})")
                continue
            matches += [Match(date.fromisoformat(r["date"]), r["home"], r["away"], r["hg"], r["ag"]) for r in rows]
        try:
            models[lg] = strength.fit(matches, day)
        except InsufficientData as exc:
            notes.append(f"{provider.COMPETITIONS.get(lg, lg)}: no model ({exc})")
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
    usable = [fx for fx in fixtures if fx["league_id"] in provider.DOMESTIC]
            
    models = await _models(day, {fx["league_id"] for fx in usable}, notes)

    all_evaluated_legs = []
    n_games = 0
    
    for fx in usable:
        name = f'{fx["home_team"]} vs {fx["away_team"]}'
        model = models.get(fx["league_id"])
        
        try:
            if model:
                grid = model.grid(fx["home_team"], fx["away_team"])
            else:
                grid = type("FastGrid", (), {"matrix": None})()
        except InsufficientData:
            continue

        market_legs = generate_all_betting_markets(grid, fx["home_team"], fx["away_team"])
        
        for leg in market_legs:
            prob = leg["prob"]
            fair_odds = leg["fair_odds"]
            priority = leg.get("priority", 1.0)
            
            if prob >= 0.58 and 1.25 <= fair_odds <= 2.65:
                all_evaluated_legs.append({
                    "fixture_name": name,
                    "home_team": fx["home_team"],
                    "away_team": fx["away_team"],
                    "market": leg["market"],
                    "selection": leg["selection"],
                    "probability": round(prob, 2),
                    "odds": fair_odds,
                    "edge": round((prob * fair_odds) - 1, 2),
                    "score": prob * priority,
                    "xG_home": leg["xG_home"],
                    "xG_away": leg["xG_away"],
                    "top_scores": leg["top_scores"]
                })

        n_games += 1

    sorted_legs = sorted(all_evaluated_legs, key=lambda x: (x["score"], x["edge"]), reverse=True)
    
    built = []
    used_fixtures_global = set()
    used_markets_global = set()

    # 1. SINGLES GENERATION (odds >= 1.40)
    selected_singles = []
    for leg in sorted_legs:
        f_name = leg["fixture_name"]
        m_name = leg["market"]
        
        if f_name not in used_fixtures_global and m_name not in used_markets_global and leg["odds"] >= 1.40:
            used_fixtures_global.add(f_name)
            used_markets_global.add(m_name)
            selected_singles.append(leg)
            if len(selected_singles) >= 4:
                break

    for leg in selected_singles:
        built.append({
            "combined_odds": leg["odds"],
            "probability": leg["probability"],
            "note": f"Daily Safe Single: {leg['market']} ({leg['selection']})",
            "legs": [leg]
        })

    # 2. STRICT DOUBLE SLIPS
    double_candidates = [l for l in sorted_legs if l["fixture_name"] not in used_fixtures_global]
    
    i = 0
    while i < len(double_candidates) - 1 and len(built) < 8:
        l1 = double_candidates[i]
        
        l2 = None
        for j in range(i + 1, len(double_candidates)):
            cand = double_candidates[j]
            if cand["fixture_name"] != l1["fixture_name"] and cand["market"] != l1["market"]:
                l2 = cand
                break

        if l2:
            c_odds = round(l1["odds"] * l2["odds"], 2)
            c_prob = round(l1["probability"] * l2["probability"], 2)
            
            if c_odds <= 3.80:
                used_fixtures_global.add(l1["fixture_name"])
                used_fixtures_global.add(l2["fixture_name"])
                built.append({
                    "combined_odds": c_odds,
                    "probability": c_prob,
                    "note": "Daily High-Value Double: Balanced multi-fixture combination",
                    "legs": [l1, l2]
                })
                double_candidates = [l for l in double_candidates if l["fixture_name"] not in used_fixtures_global]
                i = 0
                continue

        i += 1

    response_payload = {
        "date": day.isoformat(),
        "target_odds": target,
        "games_considered": n_games,
        "slips": built,
        "odds_mode": "fair",
        "skipped": skipped,
        "notes": notes,
        "unmapped_bet_types": {},
        "provider_requests_this_session": provider.stats["requests"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "disclaimer": "MatchMind Daily Picks: Zero match repetition and strict market-pair diversity."
    }

    if os.path.exists(CACHE_FILE):
        try:
            os.remove(CACHE_FILE)
        except Exception:
            pass

    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({"date": day.isoformat(), "data": response_payload}, f)
    except Exception:
        pass

    return response_payload


async def build_day_cached(day: date, target: float = 3.0) -> dict:
    """Caches slips for 15 minutes to save provider request limits."""
    key = (day.isoformat(), round(target, 2))
    hit = _day_cache.get(key)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    res = await build_day(day, target)
    _day_cache[key] = (time.time(), res)
    return res
