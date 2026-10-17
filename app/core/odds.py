"""Odds maths, value estimates, flat-stake simulation. Estimates only; never guarantees."""
from __future__ import annotations
from datetime import datetime
import numpy as np
from scipy.optimize import brentq
from app.core import metrics


def implied(odds: float) -> float:
    if odds <= 1.0:
        raise ValueError("Decimal odds must be > 1.")
    return 1.0 / odds


def overround(odds: list[float]) -> float:
    return sum(implied(o) for o in odds) - 1.0


def remove_margin(odds: list[float], method: str = "proportional") -> list[float]:
    """Margin-adjusted implied probabilities for ONE complete, mutually exclusive market."""
    if len(odds) < 2:
        raise ValueError("Need every outcome of the market (>= 2 prices).")
    q = np.array([implied(o) for o in odds])
    if method == "proportional":
        return (q / q.sum()).tolist()
    if method == "power":  # find k with sum(q^k) = 1 (favourite-longshot aware)
        if q.sum() <= 1.0:
            return (q / q.sum()).tolist()
        k = brentq(lambda k: (q ** k).sum() - 1.0, 1.0, 50.0)
        return (q ** k).tolist()
    raise ValueError("method must be 'proportional' or 'power'")


def expected_value(p: float, odds: float) -> float:
    """Estimated profit per 1 unit stake = p * odds - 1."""
    if not 0 <= p <= 1:
        raise ValueError("p must be in [0,1].")
    return p * odds - 1.0


def conservative_ev(p: float, p_se: float, odds: float, z: float = 1.0) -> float:
    return expected_value(max(0.0, p - z * p_se), odds)


def is_flagged(p, p_se, odds, odds_ts: datetime, now: datetime, *, min_prob=0.0, min_ev=0.03,
               max_odds_age_min=60, data_ok=True, calibrated=False) -> tuple[bool, list[str]]:
    """Value flag requires: fresh odds, good data, thresholds met on the CONSERVATIVE estimate."""
    why = []
    age = (now - odds_ts).total_seconds() / 60
    if age > max_odds_age_min: why.append(f"odds are {age:.0f} min old")
    if age < 0: why.append("odds timestamp is in the future")
    if not data_ok: why.append("data-quality check failed")
    if p < min_prob: why.append("probability below minimum")
    if conservative_ev(p, p_se, odds) < min_ev: why.append("conservative EV below threshold")
    if not calibrated: why.append("model not calibrated on enough data")
    return (not why, why)


def simulate_flat(bets: list[dict], seed: int = 0) -> dict:
    """bets: dict(pred_ts, odds_ts, odds, won:bool, date). 1-unit flat stake.
    Rejects any bet whose odds were quoted AFTER the prediction (look-ahead)."""
    for b in bets:
        if b["odds_ts"] > b["pred_ts"]:
            raise ValueError("Look-ahead: odds quoted after the prediction timestamp.")
    if not bets:
        return {"n_bets": 0, "note": "No bets met the criteria."}
    bets = sorted(bets, key=lambda b: b["date"])
    pnl = np.array([(b["odds"] - 1.0) if b["won"] else -1.0 for b in bets])
    cum = np.cumsum(pnl)
    dd = float((np.maximum.accumulate(np.r_[0.0, cum])[1:] - cum).max())
    lo, hi = metrics.bootstrap_ci(pnl, seed=seed)
    return {"n_bets": len(bets), "profit_units": float(pnl.sum()), "yield": float(pnl.mean()),
            "yield_ci95": [lo, hi], "max_drawdown_units": dd, "hit_rate": float(np.mean([b["won"] for b in bets])),
            "note": "Simulation, not real betting. Excludes commission/limits/slippage; small samples are unreliable."}
