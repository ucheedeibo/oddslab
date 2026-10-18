"""Settles daily slips against finished match results from provider and computes performance analytics."""
import json
import os
from datetime import date
from app import provider

def evaluate_leg(leg: dict, home_score: int, away_score: int) -> str:
    selection = leg.get("selection", "")
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

    home_team = leg.get("home_team", "")
    away_team = leg.get("away_team", "")

    if "Win" in selection and "1X" not in selection and "X2" not in selection:
        if home_team in selection and home_score > away_score:
            return "WON"
        if away_team in selection and away_score > home_score:
            return "WON"
        return "LOST"

    if "1X" in selection or "or Draw" in selection:
        return "WON" if home_score >= away_score else "LOST"
    if "X2" in selection or "Draw or" in selection:
        return "WON" if away_score >= home_score else "LOST"

    if "DNB" in selection:
        if home_score == away_score:
            return "VOID"
        if home_team in selection and home_score > away_score:
            return "WON"
        if away_team in selection and away_score > home_score:
            return "WON"
        return "LOST"

    return "PENDING"


async def settle_daily_slips(day_str: str) -> dict:
    cache_file = "daily_slips_cache.json"
    if not os.path.exists(cache_file):
        return {
            "status": "no_cache",
            "date": day_str,
            "settled_count": 0,
            "won_count": 0,
            "lost_count": 0,
            "win_rate_pct": 0.0,
            "roi_pct": 0.0,
            "slips": [],
        }

    try:
        with open(cache_file, "r") as f:
            payload = json.load(f)
    except Exception:
        payload = {}

    if payload.get("date") != day_str:
        return {
            "status": "date_mismatch",
            "date": day_str,
            "settled_count": 0,
            "won_count": 0,
            "lost_count": 0,
            "win_rate_pct": 0.0,
            "roi_pct": 0.0,
            "slips": payload.get("slips", []),
        }

    try:
        raw_finished = await provider.get_fixtures(day_str)
    except Exception:
        raw_finished = []

    score_map = {}
    for item in raw_finished:
        f = item.get("fixture", {})
        t = item.get("teams", {})
        g = item.get("goals", {})
        status_code = (f.get("status") or {}).get("short")

        if status_code in ("FT", "AET", "PEN"):
            h_name = (t.get("home") or {}).get("name")
            a_name = (t.get("away") or {}).get("name")
            hg = g.get("home")
            ag = g.get("away")
            if h_name and a_name and hg is not None and ag is not None:
                score_map[f"{h_name} vs {a_name}"] = (int(hg), int(ag))

    settled_slips = []
    total_won, total_lost, total_settled = 0, 0, 0
    total_staked, total_returns = 0.0, 0.0

    for slip in payload.get("slips", []):
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
                if res == "LOST":
                    any_lost = True
                elif res != "WON":
                    all_won = False
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
            total_returns += float(slip.get("combined_odds", 1.0))
        else:
            slip_status = "PENDING"

        slip["status"] = slip_status
        settled_slips.append(slip)

    win_rate = round((total_won / max(total_settled, 1)) * 100, 1)
    roi = round(((total_returns - total_staked) / max(total_staked, 1.0)) * 100, 1)

    return {
        "status": "success",
        "date": day_str,
        "settled_count": total_settled,
        "won_count": total_won,
        "lost_count": total_lost,
        "win_rate_pct": win_rate,
        "roi_pct": roi,
        "slips": settled_slips,
    }
