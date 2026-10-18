"""Settles daily slips against finished match results from provider."""
import os
import json
from datetime import date
from app import provider

SETTLED_FILE = "settled_slips_cache.json"

def evaluate_leg(leg: dict, home_score: int, away_score: int) -> str:
    """Evaluates an individual betting leg based on final match scores."""
    market = leg.get("market")
    selection = leg.get("selection")
    total_goals = home_score + away_score

    if "Over 1.5 Goals" in selection:
        return "WON" if total_goals > 1.5 else "LOST"
    if "Over 2.5 Goals" in selection:
        return "WON" if total_goals > 2.5 else "LOST"
    if "Under 2.5 Goals" in selection:
        return "WON" if total_goals < 2.5 else "LOST"
    if "BTTS Yes" in selection:
        return "WON" if home_score > 0 and away_score > 0 else "LOST"
    if "BTTS No" in selection:
        return "WON" if home_score == 0 or away_score == 0 else "LOST"
    if "Over 0.5 Goals" in selection:
        return "WON" if total_goals > 0.5 else "LOST"
    
    # 1X2 / Double Chance / DNB
    if "Win" in selection:
        home_won = home_score > away_score
        away_won = away_score > home_score
        if leg.get("home_team") in selection and home_won: return "WON"
        if leg.get("away_team") in selection and away_won: return "WON"
        return "LOST"
    
    if "1X" in selection or "or Draw" in selection:
        return "WON" if home_score >= away_score else "LOST"
    if "X2" in selection:
        return "WON" if away_score >= home_score else "LOST"
    if "DNB" in selection:
        if home_score == away_score: return "VOID"
        home_won = home_score > away_score
        away_won = away_score > home_score
        if leg.get("home_team") in selection and home_won: return "WON"
        if leg.get("away_team") in selection and away_won: return "WON"
        return "LOST"

    return "PENDING"


async def settle_daily_slips(day_str: str) -> dict:
    """Fetches finished fixtures for a day and grades active cached slips."""
    cache_file = "daily_slips_cache.json"
    if not os.path.exists(cache_file):
        return {"status": "no_cache", "slips": []}

    with open(cache_file, "r") as f:
        payload = json.load(f)

    if payload.get("date") != day_str:
        return {"status": "date_mismatch", "slips": payload.get("slips", [])}

    # Fetch finished matches for the day from provider
    try:
        raw_finished = await provider.get_fixtures(day_str)
    except Exception:
        raw_finished = []

    score_map = {}
    for item in raw_finished:
        f = item.get("fixture", {})
        t = item.get("teams", {})
        g = item.get("goals", {})
        if f.get("status", {}).get("short") in ("FT", "AET", "PEN"):
            h_name = (t.get("home") or {}).get("name")
            a_name = (t.get("away") or {}).get("name")
            hg = g.get("home")
            ag = g.get("away")
            if h_name and a_name and hg is not None and ag is not None:
                score_map[f"{h_name} vs {a_name}"] = (hg, ag)

    settled_slips = []
    total_won, total_lost, total_settled = 0, 0, 0
    total_staked, total_returns = 0.0, 0.0

    for slip in payload.get("slips", []):
        slip_status = "PENDING"
        graded_legs = []
        all_won = True
        any_lost = False

        for leg in slip.get("legs", []):
            fix_name = leg.get("fixture_name")
            scores = score_map.get(fix_name)
            if scores:
                hg, ag = scores
                res = evaluate_leg(leg, hg, ag)
                leg["result"] = res
                leg["final_score"] = f"{hg}-{ag}"
                if res == "LOST": any_lost = True
                elif res != "WON": all_won = False
            else:
                leg["result"] = "PENDING"
                all_won = False

            graded_legs.append(leg)

        slip["legs"] = graded_legs
        if any_lost:
            slip_status = "LOST"
            total_lost += 1
            total_settled += 1
            total_staked += 1.0
        elif all_won and graded_legs and all(l.get("result") == "WON" for l in graded_legs):
            slip_status = "WON"
            total_won += 1
            total_settled += 1
            total_staked += 1.0
            total_returns += slip.get("combined_odds", 1.0)
        else:
            slip_status = "PENDING"

        slip["status"] = slip_status
        settled_slips.append(slip)

    win_rate = round((total_won / max(total_settled, 1)) * 100, 1)
    roi = round(((total_returns - total_staked) / max(total_staked, 1.0)) * 100, 1)

    analytics = {
        "date": day_str,
        "settled_count": total_settled,
        "won_count": total_won,
        "lost_count": total_lost,
        "win_rate_pct": win_rate,
        "roi_pct": roi,
        "slips": settled_slips
    }

    return analytics
