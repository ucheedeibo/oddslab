"""Builds the daily slips.

Rules:
- Predictions come only from the fitted Poisson strength model. If a league has no
  model (or a team is unknown) the match is listed in "skipped" with a reason.
  No numbers are invented.
- Each fixture is used in at most one slip (zero match repetition).
- Provider failures are reported in the payload (status/message), never raised.
"""
import json
import logging
import os
import time
from datetime import date, datetime, timezone

from app import provider
from app.core import strength
from app.core.strength import InsufficientData, Match

log = logging.getLogger("oddslab")

CACHE_FILE = "daily_slips_cache.json"
CACHE_TTL = 900
_day_cache: dict = {}

INACTIVE_STATUSES = {"POSTPONED", "CANCELLED", "SUSPENDED", "AWARDED"}


def season_for(day: date, league_id: int = None) -> int:
    if league_id == 2013:  # Brasileirão runs on the calendar year
        return day.year
    return day.year if day.month >= 8 else day.year - 1


def _grid_matrix(grid):
    """Normalised {(home_goals, away_goals): prob}, or None when the model gave nothing usable.

    strength.StrengthModel.grid() returns a 2-D score grid (rows = home goals,
    columns = away goals). A dict keyed by (h, a), or an object holding such a
    grid in a .matrix attribute, is accepted too.
    """
    raw = getattr(grid, "matrix", grid)
    if raw is None:
        return None
    pairs = {}
    if isinstance(raw, dict):
        items = raw.items()
    else:
        try:
            rows = [[float(v) for v in row] for row in raw]
        except (TypeError, ValueError):
            return None
        items = (((h, a), p) for h, row in enumerate(rows) for a, p in enumerate(row))
    for key, p in items:
        try:
            p = float(p)
        except (TypeError, ValueError):
            continue
        if p == p and p > 0:  # drops NaN and non-positive cells
            pairs[key] = p
    total = sum(pairs.values())
    if not pairs or total <= 0:
        return None
    return {k: v / total for k, v in pairs.items()}


def _summarise(matrix: dict, top_n: int) -> dict:
    p_home = p_draw = p_away = p_btts = xg_h = xg_a = 0.0
    overs = {0.5: 0.0, 1.5: 0.0, 2.5: 0.0, 3.5: 0.0}
    for (h, a), p in matrix.items():
        if h > a:
            p_home += p
        elif h == a:
            p_draw += p
        else:
            p_away += p
        if h > 0 and a > 0:
            p_btts += p
        for line in overs:
            if h + a > line:
                overs[line] += p
        xg_h += h * p
        xg_a += a * p
    top = sorted(
        ({"score": f"{h}-{a}", "prob": round(p * 100, 1)} for (h, a), p in matrix.items()),
        key=lambda x: x["prob"],
        reverse=True,
    )[:top_n]
    return {
        "home": p_home, "draw": p_draw, "away": p_away, "btts": p_btts, "overs": overs,
        "xg_home": round(xg_h, 2), "xg_away": round(xg_a, 2), "top_scores": top,
    }


def generate_match_analysis(home_team: str, away_team: str, grid):
    """Model-backed analysis for one match, or None if the model has no data for it."""
    matrix = _grid_matrix(grid)
    if matrix is None:
        return None
    s = _summarise(matrix, 3)
    picks = [
        (f"{home_team} Win", s["home"], "1X2"),
        ("Draw", s["draw"], "1X2"),
        (f"{away_team} Win", s["away"], "1X2"),
        ("Over 2.5 Goals", s["overs"][2.5], "Over/Under"),
        ("BTTS Yes", s["btts"], "Both Teams To Score"),
    ]
    selection, prob, market = max(picks, key=lambda x: x[1])
    pct = round(prob * 100, 1)
    top = s["top_scores"]
    scores_txt = ", ".join(f"{t['score']} ({t['prob']}%)" for t in top)
    explanation = (
        f"The strength model projects expected goals of {home_team} {s['xg_home']} vs "
        f"{away_team} {s['xg_away']}. Most likely scorelines: {scores_txt}. "
        f"Its strongest lean is {selection} at {pct}% model probability."
    )
    return {
        "market": market,
        "selection": selection,
        "probability_percent": pct,
        "fair_odds": round(1 / max(prob, 0.001), 2),
        "xG_home": s["xg_home"],
        "xG_away": s["xg_away"],
        "top_scores": top,
        "explanation": explanation,
    }


def generate_all_betting_markets(grid, home_team: str, away_team: str) -> list:
    matrix = _grid_matrix(grid)
    if matrix is None:
        return []
    s = _summarise(matrix, 6)
    ph, pd, pa = s["home"], s["draw"], s["away"]
    no_draw = max(ph + pa, 0.001)
    legs = [
        {"market": "1X2", "selection": f"{home_team} Win", "prob": ph, "priority": 1.1},
        {"market": "1X2", "selection": f"{away_team} Win", "prob": pa, "priority": 1.1},
        {"market": "Double Chance", "selection": f"{home_team} or Draw (1X)", "prob": ph + pd, "priority": 1.0},
        {"market": "Double Chance", "selection": f"Draw or {away_team} (X2)", "prob": pa + pd, "priority": 1.0},
        {"market": "Draw No Bet", "selection": f"{home_team} DNB", "prob": ph / no_draw, "priority": 1.05},
        {"market": "Draw No Bet", "selection": f"{away_team} DNB", "prob": pa / no_draw, "priority": 1.05},
        {"market": "Over/Under Goals", "selection": "Over 1.5 Goals", "prob": s["overs"][1.5], "priority": 1.12},
        {"market": "Over/Under Goals", "selection": "Over 2.5 Goals", "prob": s["overs"][2.5], "priority": 1.15},
        {"market": "Both Teams To Score", "selection": "BTTS Yes", "prob": s["btts"], "priority": 1.15},
    ]
    for leg in legs:
        leg["fair_odds"] = round(1 / max(leg["prob"], 0.001), 2)
        leg["xG_home"] = s["xg_home"]
        leg["xG_away"] = s["xg_away"]
        leg["top_scores"] = s["top_scores"]
    return legs


async def _models(day: date, leagues: set, notes: list) -> dict:
    models = {}
    for lg in sorted(leagues):
        name = provider.COMPETITIONS.get(lg, lg)
        matches, failed = [], 0
        base = season_for(day, lg)
        for season in (base, base - 1):
            try:
                rows = await provider.get_finished(lg, season, strict=True)
            except RuntimeError as exc:
                failed += 1
                notes.append(f"{name} {season}: history unavailable ({exc})")
                continue
            for r in rows:
                try:
                    matches.append(Match(date.fromisoformat(r["date"]), r["home"], r["away"], r["hg"], r["ag"]))
                except (ValueError, KeyError, TypeError):
                    continue
        if not matches:
            if not failed:
                notes.append(f"{name}: no finished matches found, no model")
            continue
        try:
            models[lg] = strength.fit(matches, day)
        except InsufficientData as exc:
            notes.append(f"{name}: no model ({exc})")
        except RuntimeError as exc:
            log.error("Model fit failed for %s: %s", name, exc)
            notes.append(f"{name}: model fit failed ({exc})")
    return models


def _pick_slips(all_legs: list) -> list:
    sorted_legs = sorted(all_legs, key=lambda x: (x["score"], x["edge"]), reverse=True)
    built, used_fixtures, used_markets = [], set(), set()

    singles = []
    for leg in sorted_legs:
        if leg["fixture_name"] in used_fixtures or leg["market"] in used_markets:
            continue
        used_fixtures.add(leg["fixture_name"])
        used_markets.add(leg["market"])
        singles.append(leg)
        if len(singles) >= 4:
            break
    for leg in singles:
        built.append({
            "combined_odds": leg["odds"],
            "probability": leg["probability"],
            "note": f"Daily Safe Pick: {leg['market']} ({leg['selection']})",
            "legs": [leg],
        })

    pool = [l for l in sorted_legs if l["fixture_name"] not in used_fixtures]
    while len(built) < 8:
        if not pool:
            break
        l1 = pool[0]
        l2 = next((c for c in pool[1:] if c["fixture_name"] != l1["fixture_name"]), None)
        if l2 is None:
            break
        used_fixtures.update((l1["fixture_name"], l2["fixture_name"]))
        built.append({
            "combined_odds": round(l1["odds"] * l2["odds"], 2),
            "probability": round(l1["probability"] * l2["probability"], 2),
            "note": "Daily Super Pick Combo: Multi-fixture value slip",
            "legs": [l1, l2],
        })
        pool = [l for l in pool if l["fixture_name"] not in used_fixtures]
    return built


def _write_cache(day: date, payload: dict) -> None:
    tmp = CACHE_FILE + ".tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump({"date": day.isoformat(), "data": payload}, fh)
        os.replace(tmp, CACHE_FILE)
    except Exception as exc:
        log.error("Could not write %s: %s", CACHE_FILE, exc)


async def build_day(day: date, target: float = 3.0, max_fixtures: int = 50) -> dict:
    notes, skipped = [], []
    raw, error = await provider.get_fixtures_checked(day.isoformat())
    if error:
        notes.append(error["message"])
    fixtures = [provider.flatten_fixture(item) for item in raw][:max_fixtures]

    active = []
    for fx in fixtures:
        if fx["status"] in INACTIVE_STATUSES:
            skipped.append({**fx, "reason": f"Match status is {fx['status']}"})
        elif fx["league_id"] not in provider.MODELLED:
            skipped.append({**fx, "reason": "No season-long model for this competition"})
        else:
            active.append(fx)

    models = await _models(day, {fx["league_id"] for fx in active}, notes) if active else {}

    predictions, all_legs = [], []
    for fx in active:
        home, away = fx["home_team"], fx["away_team"]
        model = models.get(fx["league_id"])
        if model is None:
            skipped.append({**fx, "reason": "No strength model available for this league"})
            continue
        try:
            grid = model.grid(home, away)
        except InsufficientData as exc:
            skipped.append({**fx, "reason": f"Not enough history for these teams ({exc})"})
            continue
        analysis = generate_match_analysis(home, away, grid)
        if analysis is None:
            skipped.append({**fx, "reason": "Model returned no score grid"})
            continue
        fx["prediction"] = analysis
        predictions.append(fx)

        for leg in generate_all_betting_markets(grid, home, away):
            prob = leg["prob"]
            all_legs.append({
                "fixture_name": f"{home} vs {away}",
                "home_team": home,
                "away_team": away,
                "market": leg["market"],
                "selection": leg["selection"],
                "probability": round(prob, 2),
                "odds": leg["fair_odds"],
                "edge": round(prob * leg["fair_odds"] - 1, 2),
                "score": prob * leg["priority"],
                "xG_home": leg["xG_home"],
                "xG_away": leg["xG_away"],
                "top_scores": leg["top_scores"],
            })

    slips = _pick_slips(all_legs)

    if error:
        status, message = "provider_error", error["message"]
    elif not fixtures:
        status, message = "no_fixtures", provider.no_fixtures_message(day)
    elif not predictions:
        status = "no_predictions"
        message = "Matches were found, but there is not enough data to model them yet. See the notes."
    else:
        status, message = "ok", None

    payload = {
        "date": day.isoformat(),
        "status": status,
        "message": message,
        "target_odds": target,
        "fixtures_total": len(fixtures),
        "games_considered": len(predictions),
        "match_predictions": predictions,
        "slips": slips,
        "odds_mode": "fair",
        "skipped": skipped,
        "notes": notes,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "disclaimer": "Probabilities come from a Poisson strength model. Odds shown are model fair odds, not bookmaker prices. Not a guarantee of any result.",
    }
    if status == "ok" and slips:
        _write_cache(day, payload)
    return payload


async def build_day_cached(day: date, target: float = 3.0) -> dict:
    key = (day.isoformat(), round(target, 2))
    now = time.time()
    hit = _day_cache.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    res = await build_day(day, target)
    for k in [k for k, v in _day_cache.items() if now - v[0] >= CACHE_TTL]:
        del _day_cache[k]
    if res["status"] in ("ok", "no_fixtures"):  # never cache failures
        _day_cache[key] = (now, res)
    return res
