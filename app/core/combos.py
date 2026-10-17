"""Accumulator (multi-leg) candidate search around a target combined price.

Dependence assumption: legs from DIFFERENT fixtures are treated as independent (an approximation:
same-day/same-league effects exist but are small relative to model error). Legs from the SAME fixture
are never combined. Uncertainty is carried by a conservative probability p - z*se per leg.
"""
from __future__ import annotations
from itertools import combinations
import math

EXCLUSIVE = {"home_win", "draw", "away_win"}


def combined_odds(odds: list[float]) -> float:
    return math.prod(odds)


def search(legs: list[dict], target: float = 3.0, tolerance: float = 0.25, min_legs: int = 2, max_legs: int = 4,
           z: float = 1.0, top: int = 5, max_candidates: int = 60) -> list[dict]:
    """legs: dict(fixture_id, market, selection, odds, p, p_se). Returns ranked candidates (conservative EV)."""
    seen = set()
    clean = []
    for l in legs:
        key = (l["fixture_id"], l["market"], l["selection"])
        if key in seen or l["odds"] <= 1.0 or not 0 < l["p"] < 1:
            continue
        seen.add(key); clean.append(l)
    # keep a bounded pool: best conservative per-leg EV first (does NOT simply pick most-likely legs)
    clean.sort(key=lambda l: (max(0, l["p"] - z * l["p_se"]) * l["odds"]), reverse=True)
    pool = clean[:max_candidates]
    lo, hi = target * (1 - tolerance / 3), target * (1 + tolerance / 3)
    out = []
    for r in range(min_legs, max_legs + 1):
        for combo in combinations(pool, r):
            if len({l["fixture_id"] for l in combo}) < r:
                continue  # never two legs from one match
            price = combined_odds([l["odds"] for l in combo])
            if not lo <= price <= hi:
                continue
            p = math.prod(l["p"] for l in combo)
            p_cons = math.prod(max(0.0, l["p"] - z * l["p_se"]) for l in combo)
            out.append({"legs": list(combo), "combined_odds": price, "probability": p,
                        "probability_conservative": p_cons, "ev": p * price - 1, "ev_conservative": p_cons * price - 1,
                        "assumption": "Independent across different fixtures (approximation)."})
    out.sort(key=lambda c: c["ev_conservative"], reverse=True)
    for c in out[:top]:
        c["note"] = ("Conservative EV is positive, but it still rests on uncalibrated/unvalidated estimates."
                     if c["ev_conservative"] > 0 else
                     "No edge after allowing for uncertainty: reaching the target price does not make this safe.")
    return out[:top]
