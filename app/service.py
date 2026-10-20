"""Daily match predictions and target-odds slips.

Rules:
- Every probability comes from the fitted strength model. Matches the model cannot
  rate are listed in "skipped" with a reason. No numbers are invented.
- Predictions (every rated match) are separate from slips (the daily picks).
- Slips only use matches rated with High or Medium confidence, use each match at
  most once, and aim for the chosen target odds.
- Champions League matches are rated with a pooled model that also learns from
  the big domestic leagues, because a single competition gives too little data.
"""
import asyncio
import json
import logging
import math
import os
import time
from datetime import date, datetime, timezone

from app import provider
from app.core import strength
from app.core.strength import InsufficientData, Match

log = logging.getLogger("oddslab")

CACHE_FILE = "daily_slips_cache.json"
CACHE_TTL = 900
_analysis_cache: dict = {}

INACTIVE_STATUSES = {"POSTPONED", "CANCELLED", "SUSPENDED", "AWARDED"}
CL_ID = 2001
# Competitions whose results feed the pooled Champions League model.
POOL_IDS = (2001, 2021, 2014, 2019, 2002, 2015, 2003, 2017)

HIGH_MATCHES = 12
MEDIUM_MATCHES = 6

# Slip construction
MAX_LEGS = 3
MIN_LEG_ODDS = 1.20
MIN_LEG_PROB = 0.30
TARGET_LOW, TARGET_HIGH = 0.90, 1.12
MAX_SLIPS = 4
LEGS_PER_FIXTURE = 6


def season_for(day: date, league_id: int = None) -> int:
    if league_id == 2013:  # Brasileirão runs on the calendar year
        return day.year
    return day.year if day.month >= 8 else day.year - 1


def history_pairs(day: date, league_id: int) -> list:
    """(competition, season) results needed to rate fixtures of this competition."""
    ids = POOL_IDS if league_id == CL_ID else (league_id,)
    pairs = []
    for lg in ids:
        base = season_for(day, lg)
        pairs += [(lg, base), (lg, base - 1)]
    return pairs


def _to_matches(row_lists) -> list:
    out = []
    for rows in row_lists:
        for r in rows or []:
            try:
                out.append(Match(date.fromisoformat(r["date"]), r["home"], r["away"], r["hg"], r["ag"]))
            except (ValueError, KeyError, TypeError):
                continue
    return out


# ---------- score grid maths ----------

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
        "p_home": round(s["home"] * 100, 1),
        "p_draw": round(s["draw"] * 100, 1),
        "p_away": round(s["away"] * 100, 1),
        "p_over_2_5": round(s["overs"][2.5] * 100, 1),
        "p_btts": round(s["btts"] * 100, 1),
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


def confidence_for(model, home: str, away: str):
    """High / Medium / Low from the prior matches of the less-established team."""
    n = min(model.counts.get(home, 0), model.counts.get(away, 0))
    if n >= HIGH_MATCHES:
        return "High", n
    if n >= MEDIUM_MATCHES:
        return "Medium", n
    return "Low", n


# ---------- history and models ----------

async def _load_history(pairs: list, notes: list, allow_fetch: bool):
    """Returns ({pair: rows}, pending_pairs). Missing history is fetched in the background."""
    rows, pending = {}, []
    fetching = allow_fetch
    for pair in pairs:
        cached = provider.cached_finished(*pair)
        if cached is not None:
            rows[pair] = cached
            continue
        name = provider.COMPETITIONS.get(pair[0], pair[0])
        if provider.recently_failed(pair):
            notes.append(f"{name} {pair[1]}: history unavailable")
            continue
        if fetching:
            try:
                rows[pair] = await provider.get_finished(pair[0], pair[1], strict=True, max_wait=8)
                continue
            except provider.ProviderError as exc:
                if exc.code == "rate_limited":
                    fetching = False
                else:
                    notes.append(f"{name} {pair[1]}: history unavailable ({exc})")
                    continue
        pending.append(pair)
    if pending:
        provider.prefetch_background(pending)
    return rows, pending


async def _fit_models(day: date, league_ids: set, notes: list):
    models, pending_leagues = {}, set()
    for lg in sorted(league_ids):
        name = provider.COMPETITIONS.get(lg, lg)
        rows, pending = await _load_history(history_pairs(day, lg), notes, allow_fetch=(lg != CL_ID))
        if pending:
            pending_leagues.add(lg)
            continue
        matches = _to_matches(rows.values())
        if not matches:
            notes.append(f"{name}: no finished matches found, no model")
            continue
        try:
            models[lg] = await asyncio.to_thread(strength.fit, matches, day)
        except InsufficientData as exc:
            notes.append(f"{name}: no model ({exc})")
        except RuntimeError as exc:
            log.error("Model fit failed for %s: %s", name, exc)
            notes.append(f"{name}: model fit failed ({exc})")
    return models, pending_leagues


async def warm_up(day: date = None) -> None:
    """Start loading history in the background (called when the app starts)."""
    day = day or datetime.now(timezone.utc).date()
    pairs = []
    for lg in provider.MODELLED:
        for pair in history_pairs(day, lg):
            if pair not in pairs:
                pairs.append(pair)
    queued = provider.prefetch_background(pairs)
    if queued:
        log.info("Warm-up: loading %s history sets in the background", queued)


# ---------- analysis of one day ----------

async def analyse_day(day: date, use_cache: bool = True, max_fixtures: int = 60) -> dict:
    now = time.time()
    hit = _analysis_cache.get(day)
    if use_cache and hit and now - hit[0] < CACHE_TTL:
        return hit[1]

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

    models, pending = await _fit_models(day, {fx["league_id"] for fx in active}, notes) if active else ({}, set())

    predictions, legs = [], []
    for fx in active:
        home, away = fx["home_team"], fx["away_team"]
        model = models.get(fx["league_id"])
        if model is None:
            if fx["league_id"] in pending:
                reason = "League history is still loading in the background. Refresh in a minute or two."
            else:
                reason = "No strength model available for this competition"
            skipped.append({**fx, "reason": reason})
            continue
        try:
            grid = model.grid(home, away, min_matches=1)
        except InsufficientData as exc:
            skipped.append({**fx, "reason": f"No match history for these teams ({exc})"})
            continue
        analysis = generate_match_analysis(home, away, grid)
        if analysis is None:
            skipped.append({**fx, "reason": "Model returned no score grid"})
            continue
        conf, n = confidence_for(model, home, away)
        analysis["explanation"] += f" Confidence: {conf} ({n} prior matches for the less-established team)."
        fx["prediction"] = analysis
        fx["confidence"] = conf
        fx["matches_used"] = n
        predictions.append(fx)

        for leg in generate_all_betting_markets(grid, home, away):
            prob = leg["prob"]
            legs.append({
                "fixture_name": f"{home} vs {away}",
                "home_team": home,
                "away_team": away,
                "market": leg["market"],
                "selection": leg["selection"],
                "probability": round(prob, 2),
                "odds": leg["fair_odds"],
                "edge": round(prob * leg["fair_odds"] - 1, 2),
                "score": prob * leg["priority"],
                "confidence": conf,
                "xG_home": leg["xG_home"],
                "xG_away": leg["xG_away"],
                "top_scores": leg["top_scores"],
            })

    predictions.sort(key=lambda f: f.get("kickoff") or "")

    if error:
        status, message = "provider_error", error["message"]
    elif not fixtures:
        status, message = "no_fixtures", provider.no_fixtures_message(day)
    elif not predictions and pending:
        status = "loading"
        message = "Loading league history in the background (the free data plan is rate-limited). Try again in a minute or two."
    elif not predictions:
        status = "no_predictions"
        message = "Matches were found, but there is not enough data to model them yet. See the notes."
    else:
        status, message = "ok", None

    result = {
        "date": day.isoformat(),
        "status": status,
        "message": message,
        "fixtures_total": len(fixtures),
        "predictions": predictions,
        "legs": legs,
        "skipped": skipped,
        "notes": notes,
        "loading": bool(pending),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if status in ("ok", "no_fixtures", "no_predictions") and not pending:
        _analysis_cache[day] = (now, result)
        for d in [d for d, v in _analysis_cache.items() if now - v[0] >= CACHE_TTL]:
            del _analysis_cache[d]
    return result


# ---------- slips aimed at a target ----------

def _best_combo(groups: dict, target: float):
    keys = list(groups)
    lo, hi = target * TARGET_LOW, target * TARGET_HIGH
    best_in, best_close = [None, None], [None, None]

    def consider(chosen, prod):
        closeness = abs(math.log(prod / target))
        legs_gap = abs(len(chosen) - 2)  # two legs preferred: errors compound with every extra leg
        weakest = min(l["probability"] for l in chosen)
        if lo <= prod <= hi:
            k = (legs_gap, -weakest, closeness)
            if best_in[0] is None or k < best_in[0]:
                best_in[0], best_in[1] = k, (list(chosen), prod)
        k2 = (closeness, legs_gap)
        if best_close[0] is None or k2 < best_close[0]:
            best_close[0], best_close[1] = k2, (list(chosen), prod)

    def rec(start, chosen, prod):
        if chosen:
            consider(chosen, prod)
        if len(chosen) >= MAX_LEGS:
            return
        for j in range(start, len(keys)):
            for leg in groups[keys[j]]:
                p = prod * leg["odds"]
                if p > hi:
                    continue
                chosen.append(leg)
                rec(j + 1, chosen, p)
                chosen.pop()

    rec(0, [], 1.0)
    if best_in[1] is not None:
        return best_in[1][0], best_in[1][1], True
    if best_close[1] is not None:
        return best_close[1][0], best_close[1][1], False
    return None, None, False


def build_slips(legs: list, target: float, max_slips: int = MAX_SLIPS) -> list:
    """Slips near `target` total odds. Each match appears in at most one slip."""
    groups = {}
    for leg in sorted(legs, key=lambda l: l["score"], reverse=True):
        if leg["confidence"] == "Low" or leg["odds"] < MIN_LEG_ODDS or leg["probability"] < MIN_LEG_PROB:
            continue
        g = groups.setdefault(leg["fixture_name"], [])
        if len(g) < LEGS_PER_FIXTURE:
            g.append(leg)

    slips = []
    while groups and len(slips) < max_slips:
        combo, prod, meets = _best_combo(groups, target)
        if not combo:
            break
        probability = 1.0
        for leg in combo:
            probability *= leg["probability"]
        label = f"Target ~{target:.2f}: {len(combo)}-leg slip"
        if not meets:
            label = f"Closest available to ~{target:.2f} (today's matches cannot reach it)"
        slips.append({
            "combined_odds": round(prod, 2),
            "probability": round(probability, 2),
            "note": label,
            "meets_target": meets,
            "legs": combo,
        })
        for leg in combo:
            groups.pop(leg["fixture_name"], None)
        if not meets:
            break
    return slips


def _write_cache(day: date, payload: dict) -> None:
    tmp = CACHE_FILE + ".tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump({"date": day.isoformat(), "data": payload}, fh)
        os.replace(tmp, CACHE_FILE)
    except Exception as exc:
        log.error("Could not write %s: %s", CACHE_FILE, exc)


DISCLAIMER = ("Probabilities come from a Poisson strength model fitted to past results. "
              "Odds shown are model fair odds, not bookmaker prices. Not a guarantee of any result.")


async def build_day(day: date, target: float = 3.0, use_cache: bool = False) -> dict:
    a = await analyse_day(day, use_cache=use_cache)
    slips = build_slips(a["legs"], target)
    status, message = a["status"], a["message"]
    if status == "ok" and not slips:
        status = "no_slips"
        message = (f"No combination of today's well-rated matches gets near ~{target:.2f}. "
                   "Try another target, or open All Match Predictions.")
    elif status == "ok" and slips and not all(s["meets_target"] for s in slips):
        message = f"Today's matches cannot reach ~{target:.2f} exactly; showing the closest available."
    payload = {
        "date": a["date"],
        "status": status,
        "message": message,
        "target_odds": target,
        "fixtures_total": a["fixtures_total"],
        "games_considered": len(a["predictions"]),
        "slips": slips,
        "odds_mode": "fair",
        "skipped": a["skipped"],
        "notes": a["notes"],
        "generated_at": a["generated_at"],
        "disclaimer": DISCLAIMER,
    }
    if slips:
        _write_cache(day, payload)
    return payload


async def build_day_cached(day: date, target: float = 3.0) -> dict:
    return await build_day(day, target, use_cache=True)


async def build_predictions(day: date, use_cache: bool = True) -> dict:
    a = await analyse_day(day, use_cache=use_cache)
    return {
        "date": a["date"],
        "status": a["status"],
        "message": a["message"],
        "count": len(a["predictions"]),
        "fixtures_total": a["fixtures_total"],
        "predictions": a["predictions"],
        "skipped": a["skipped"],
        "notes": a["notes"],
        "generated_at": a["generated_at"],
        "disclaimer": DISCLAIMER,
    }
