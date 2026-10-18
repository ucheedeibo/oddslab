"""Orchestrates daily slip building with comprehensive market coverage and selection diversity."""
import asyncio
import json
import os
from datetime import date, datetime, timezone
from math import exp, factorial
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


def poisson_prob(lmbda, k):
    return (exp(-lmbda) * (lmbda ** k)) / factorial(k)


def generate_all_betting_markets(grid, home_team, away_team):
    """Evaluates dynamic probabilities across primary and extended markets."""
    matrix = getattr(grid, "matrix", None)
    
    if not matrix:
        lam_home = getattr(grid, "lam_home", 1.45)
        lam_away = getattr(grid, "lam_away", 1.15)
        matrix = {}
        for h in range(6):
            for a in range(6):
                matrix[(h, a)] = poisson_prob(lam_home, h) * poisson_prob(lam_away, a)

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

    # 1. MATCH RESULT & DOUBLE CHANCE (High Priority)
    legs.extend([
        {"market": "1X2", "selection": f"{home_team} Win", "prob": p_home, "priority": 1.1},
        {"market": "1X2", "selection": f"{away_team} Win", "prob": p_away, "priority": 1.1},
        {"market": "Double Chance", "selection": f"{home_team} or Draw (1X)", "prob": p_home + p_draw, "priority": 1.2},
        {"market": "Double Chance", "selection": f"Draw or {away_team} (X2)", "prob": p_away + p_draw, "priority": 1.2},
        {"market": "Draw No Bet", "selection": f"{home_team} DNB", "prob": p_home / max(p_home + p_away, 0.001), "priority": 1.0},
    ])

    # 2. GOALS & BTTS (High Priority)
    legs.extend([
        {"market": "Over/Under Goals", "selection": "Over 1.5 Goals", "prob": goals_over[1.5], "priority": 1.0},
        {"market": "Over/Under Goals", "selection": "Over 2.5 Goals", "prob": goals_over[2.5], "priority": 1.0},
        {"market": "Over/Under Goals", "selection": "Under 2.5 Goals", "prob": 1 - goals_over[2.5], "priority": 1.0},
        {"market": "Both Teams To Score", "selection": "BTTS Yes", "prob": p_btts, "priority": 1.05},
        {"market": "Both Teams To Score", "selection": "BTTS No", "prob": 1 - p_btts, "priority": 1.05},
        {"market": "Team Goals", "selection": f"{home_team} Over 0.5 Goals", "prob": home_score_over[0.5], "priority": 1.0},
        {"market": "Team Goals", "selection": f"{away_team} Over 0.5 Goals", "prob": away_score_over[0.5], "priority": 1.0},
    ])

    # 3. HANDICAPS & COMBINED
    legs.extend([
        {"market": "Asian Handicap", "selection": f"{home_team} (-1.0)", "prob": p_home * 0.65, "priority": 0.9},
        {"market": "Asian Handicap", "selection": f"{away_team} (+1.0)", "prob": (p_away + p_draw) * 0.85, "priority": 0.9},
        {"market": "Match Result & Over/Under", "selection": f"{home_team} Win & Over 1.5 Goals", "prob": p_home * goals_over[1.5], "priority": 0.85},
    ])

    # Compute fair odds
    for leg in legs:
        leg["fair_odds"] = 1 / max(leg["prob"], 0.001)

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
        
        try:
            if model:
                grid = model.grid(fx["home_team"], fx["away_team"])
            else:
                grid = type("FastGrid", (), {"lam_home": 1.4, "lam_away": 1.1, "matrix": None})()
        except InsufficientData:
            continue

        if use_odds and not isinstance(resp, Exception):
            odds_map, un = parse_odds(resp)
            for k, v in un.items(): unmapped[k] = unmapped.get(k, 0) + v
        else:
            odds_map = {}

        market_legs = generate_all_betting_markets(grid, fx["home_team"], fx["away_team"])
        
        for leg in market_legs:
            prob = leg["prob"]
            fair_odds = leg["fair_odds"]
            priority = leg.get("priority", 1.0)
            market_key = f"{leg['market']}_{leg['selection']}"
            bookmaker_price = odds_map.get(market_key, fair_odds)
            
            # Risk Management: Min 58% probability, Max Odds 2.70
            if prob >= 0.58 and 1.18 <= bookmaker_price <= 2.70:
                all_evaluated_legs.append({
                    "fixture_name": name,
                    "home_team": fx["home_team"],
                    "away_team": fx["away_team"],
                    "market": leg["market"],
                    "selection": leg["selection"],
                    "probability": prob,
                    "odds": bookmaker_price,
                    "edge": (prob * bookmaker_price) - 1,
                    "score": prob * priority  # Priority weighted ranking score
                })

        n_games += 1

    # Sort legs by priority-weighted score
    sorted_legs = sorted(all_evaluated_legs, key=lambda x: (x["score"], x["edge"]), reverse=True)
    
    # Selection Filtering: Enforce match diversity AND market type diversity
    built = []
    used_fixtures = set()
    used_markets = set()
    selected_singles = []

    for leg in sorted_legs:
        f_name = leg["fixture_name"]
        m_name = leg["market"]
        
        # Max 1 pick per fixture for singles, and ensure market variety
        if f_name not in used_fixtures and (used_markets.count(m_name) if hasattr(used_markets, 'count') else list(used_markets).count(m_name)) < 2:
            used_fixtures.add(f_name)
            used_markets.add(m_name)
            selected_singles.append(leg)
            if len(selected_singles) >= 5:
                break

    for leg in selected_singles:
        built.append({
            "combined_odds": leg["odds"],
            "probability": leg["probability"],
            "note": f"Daily Safe Single: {leg['market']} ({leg['selection']})",
            "legs": [leg]
        })

    # Strict Doubles from distinct remaining fixtures
    double_candidates = [l for l in sorted_legs if l["fixture_name"] not in used_fixtures][:6]
    if len(double_candidates) >= 2:
        for i in range(0, min(len(double_candidates) - 1, 4), 2):
            l1, l2 = double_candidates[i], double_candidates[i+1]
            c_odds = l1["odds"] * l2["odds"]
            c_prob = l1["probability"] * l2["probability"]
            if c_odds <= 3.80:
                built.append({
                    "combined_odds": c_odds,
                    "probability": c_prob,
                    "note": "Daily High-Value Double: Balanced multi-fixture combination",
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
        "disclaimer": "MatchMind Daily Picks: Ranked across diverse fixtures and high-value primary markets."
    }

    # Remove old cache file to force fresh generation
    if os.path.exists(CACHE_FILE):
        try:
            os.remove(CACHE_FILE)
        except Exception:
            pass

    save_daily_cache(day, response_payload)
    return response_payload
