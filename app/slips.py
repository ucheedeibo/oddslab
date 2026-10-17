"""Build daily slips (multi-leg combinations near a target price). Pure logic: no network, no web framework."""
from __future__ import annotations
import math
from app.core import combos, odds as odds_math, pricing

GROUPS = {"1X2": [("1X2", "H"), ("1X2", "D"), ("1X2", "A")], "BTTS": [("BTTS", "Y"), ("BTTS", "N")]}


def market_implied(odds_map: dict) -> dict:
    """Margin-removed implied probabilities, only where the whole market is present."""
    out = {}
    groups = [g for g in GROUPS.values()]
    for mk, lines in (("OU", pricing.OU_LINES), ("TTH", pricing.TT_LINES), ("TTA", pricing.TT_LINES)):
        groups += [[(mk, f"O{L}"), (mk, f"U{L}")] for L in lines]
    for g in groups:
        if all(k in odds_map for k in g):
            ps = odds_math.remove_margin([odds_map[k]["odds"] for k in g], "proportional")
            out.update(dict(zip(g, ps)))
    return out


def legs_for_fixture(fx: dict, grid, odds_map: dict, n_eff: int, disagree: float = 0.12) -> list[dict]:
    probs = pricing.selection_probs(grid)
    implied = market_implied(odds_map)
    legs = []
    for key, o in odds_map.items():
        if key not in probs:
            continue
        p = min(max(probs[key], 1e-6), 1 - 1e-6)
        
        # STRICT LIKELIHOOD FILTER: Require model probability > 80% (p >= 0.80)
        if p < 0.80:
            continue
            
        if o.get("n_books", 1) < 2:
            continue

        se = max(0.03, math.sqrt(p * (1 - p) / max(n_eff, 1)))
        leg = {"fixture_id": fx["fixture_id"], "market": key[0], "selection": key[1],
               "label": pricing.label(key[0], key[1], fx["home_team"], fx["away_team"]),
               "match": f'{fx["home_team"]} vs {fx["away_team"]}', "league": fx["league"], "kickoff": fx["kickoff"],
               "odds": o["odds"], "n_books": o["n_books"], "p": p, "p_se": se,
               "p_market": implied.get(key), "warn": None}
               
        if leg["p_market"] is not None and abs(p - leg["p_market"]) > disagree:
            continue
            
        legs.append(leg)
    return legs


def select_slips(cands: list[dict], max_slips: int, max_shared: int = 0) -> list[dict]:
    """Pick distinct slips with zero shared matches to prevent repeating games."""
    chosen = []
    for c in cands:
        ids = {l["fixture_id"] for l in c["legs"]}
        if all(len(ids & {l["fixture_id"] for l in s["legs"]}) <= max_shared for s in chosen):
            chosen.append(c)
        if len(chosen) >= max_slips:
            break
    return chosen


def build_slips(legs: list[dict], n_games: int, target: float = 2.8) -> list[dict]:
    if n_games < 2:
        return []
    cands = combos.search(legs, target=target, top=400)
    slips = select_slips(cands, max_slips=max(1, min(6, n_games // 3)), max_shared=0)
    for s in slips:
        s["value_flag"] = s["ev_conservative"] > 0
        s["warnings"] = sorted({l["warn"] for l in s["legs"] if l["warn"]})
    return slips
