"""Settles saved daily slips against finished results and computes performance.

Slips are saved once per day and tier (slipstore), graded when results arrive, and written
back once final, so a finished day never calls the football data API again.
Returns are at model fair odds, so ROI here measures calibration, not bookmaker profit.
"""
import logging
import time
from datetime import date

from app import provider, slipstore

log = logging.getLogger("oddslab")

FINAL = {"FINISHED", "FT", "AET", "PEN"}  # football-data.org says FINISHED
CHECK_EVERY = 1800  # seconds between result lookups for a day that is still open


def _by_text(leg: dict, home_score: int, away_score: int) -> str:
    """Fallback for slips saved without a base_key."""
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
    home_team, away_team = leg.get("home_team", ""), leg.get("away_team", "")
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


def evaluate_leg(leg: dict, home_score: int, away_score: int) -> str:
    """WON, LOST, VOID or PENDING. Uses the leg's base_key, so team names never matter."""
    h, a, total = home_score, away_score, home_score + away_score
    wins = {"home": h > a, "away": a > h, "dc1x": h >= a, "dcx2": a >= h,
            "over15": total > 1.5, "over25": total > 2.5, "btts": h > 0 and a > 0}
    key = leg.get("base_key")
    if key in wins:
        return "WON" if wins[key] else "LOST"
    if key in ("dnb_home", "dnb_away"):
        if h == a:
            return "VOID"
        return "WON" if (h > a) == (key == "dnb_home") else "LOST"
    return _by_text(leg, home_score, away_score)


async def _scores(day_str: str):
    """{('id', fixture_id) or ('name', 'A vs B'): (home, away)} for finished matches, None on failure."""
    try:
        items = await provider.get_fixtures(day_str)
    except Exception as exc:
        log.warning("Could not fetch results for %s: %s", day_str, exc)
        return None
    out = {}
    for item in items:
        f, t, g = item.get("fixture") or {}, item.get("teams") or {}, item.get("goals") or {}
        if (f.get("status") or {}).get("short") not in FINAL:
            continue
        hg, ag = g.get("home"), g.get("away")
        if hg is None or ag is None:
            continue
        out[("id", f.get("id"))] = (int(hg), int(ag))
        h, a = (t.get("home") or {}).get("name"), (t.get("away") or {}).get("name")
        if h and a:
            out[("name", f"{h} vs {a}")] = (int(hg), int(ag))
    return out


def _grade(doc: dict, scores: dict) -> None:
    for tier in doc["tiers"].values():
        for slip in tier["slips"]:
            lost = pending = False
            returned, voids = 1.0, 0
            for leg in slip["legs"]:
                sc = scores.get(("id", leg.get("fixture_id"))) or scores.get(("name", leg.get("fixture_name")))
                if sc:
                    leg["result"], leg["final_score"] = evaluate_leg(leg, *sc), f"{sc[0]}-{sc[1]}"
                res = leg.setdefault("result", "PENDING")  # keeps earlier grades if a lookup fails
                if res == "LOST":
                    lost = True
                elif res == "VOID":
                    voids += 1
                elif res == "WON":
                    returned *= float(leg.get("odds") or 1.0)
                else:
                    pending = True
            if lost:
                slip["status"], slip["returned"] = "LOST", 0.0
            elif pending:
                slip["status"], slip["returned"] = "PENDING", 0.0
            elif voids == len(slip["legs"]):
                slip["status"], slip["returned"] = "VOID", 1.0
            else:  # a void leg simply drops out of the odds
                slip["status"], slip["returned"] = "WON", round(returned, 2)


def _stats(slips: list) -> dict:
    st = [s.get("status", "PENDING") for s in slips]  # ungraded slips count as pending
    won, lost = st.count("WON"), st.count("LOST")
    settled = won + lost
    returned = sum(s.get("returned", 0.0) for s in slips if s.get("status") == "WON")
    return {
        "settled_count": settled, "won_count": won, "lost_count": lost,
        "void_count": st.count("VOID"), "pending_count": st.count("PENDING"),
        "win_rate_pct": round(won / settled * 100, 1) if settled else 0.0,
        "roi_pct": round((returned - settled) / settled * 100, 1) if settled else 0.0,
        "staked": float(settled), "returned": round(returned, 2),
        # names main.get_cumulative_performance reads
        "total": settled, "won": won,
    }


def _summary(day_str: str, doc: dict) -> dict:
    flat, tiers = [], {}
    for key, tier in doc["tiers"].items():
        tiers[key] = _stats(tier["slips"])
        flat += [{**s, "tier": key} for s in tier["slips"]]
    return {"status": "success", "date": day_str, **_stats(flat), "tiers": tiers, "slips": flat}


async def settle_daily_slips(day_str: str) -> dict:
    doc = slipstore.load(day_str)
    if not doc or not doc.get("tiers"):
        return {"status": "no_slips", "date": day_str, **_stats([]), "tiers": {}, "slips": []}
    try:
        started = date.fromisoformat(day_str) <= date.today()
    except ValueError:
        started = False
    if started and not doc.get("final") and time.time() - doc.get("checked_ts", 0) >= CHECK_EVERY:
        scores = await _scores(day_str)
        if scores is not None:
            _grade(doc, scores)
            doc["checked_ts"] = time.time()
            doc["final"] = all(s["status"] != "PENDING" for t in doc["tiers"].values() for s in t["slips"])
            slipstore.write(day_str, doc)
    return _summary(day_str, doc)