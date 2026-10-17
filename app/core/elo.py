"""Elo ratings with a Davidson draw model (so Elo yields H/D/A, not just win expectation)."""
from __future__ import annotations
from datetime import date
from math import log
import numpy as np
from scipy.optimize import brentq
from app.core.strength import Match, InsufficientData


def davidson(diff: float, nu: float) -> tuple[float, float, float]:
    """diff = rating difference incl. home advantage (home - away)."""
    ph, pa = 10 ** (diff / 800), 10 ** (-diff / 800)
    d = nu
    z = ph + pa + d
    return ph / z, d / z, pa / z


class EloModel:
    version = "elo-davidson-v1"

    def __init__(self, ratings, home_adv, nu, counts, as_of, n):
        self.ratings, self.home_adv, self.nu, self.counts, self.as_of, self.n_matches = ratings, home_adv, nu, counts, as_of, n

    def predict(self, home: str, away: str, min_matches: int = 5) -> dict:
        for t in (home, away):
            if self.counts.get(t, 0) < min_matches:
                raise InsufficientData(f"{t}: only {self.counts.get(t, 0)} prior matches (need {min_matches}).")
        h, d, a = davidson(self.ratings[home] - self.ratings[away] + self.home_adv, self.nu)
        return {"home_win": h, "draw": d, "away_win": a, "model_version": self.version,
                "trained_through": self.as_of.isoformat(), "matches_used": self.n_matches}


def fit(matches: list[Match], as_of: date, k: float = 20.0, home_adv: float = 65.0, min_total: int = 30,
        window_days: int | None = None) -> EloModel:
    used = sorted([m for m in matches if m.date < as_of and m.hg is not None and m.ag is not None
                   and (window_days is None or (as_of - m.date).days <= window_days)], key=lambda m: m.date)
    if len(used) < min_total:
        raise InsufficientData(f"{len(used)} completed matches before {as_of}; need {min_total}.")
    draw_rate = float(np.mean([m.hg == m.ag for m in used]))
    # nu such that a level match has draw probability == observed draw rate
    nu = draw_rate * 2 / (1 - draw_rate)
    r: dict[str, float] = {}
    counts: dict[str, int] = {}
    for m in used:
        rh, ra = r.get(m.home, 1500.0), r.get(m.away, 1500.0)
        ph, pd_, pa = davidson(rh - ra + home_adv, nu)
        exp_home = ph + 0.5 * pd_
        score = 1.0 if m.hg > m.ag else 0.5 if m.hg == m.ag else 0.0
        mult = 1.0 + 0.5 * log(1 + abs(m.hg - m.ag)) if m.hg != m.ag else 1.0
        delta = k * mult * (score - exp_home)
        r[m.home], r[m.away] = rh + delta, ra - delta
        counts[m.home] = counts.get(m.home, 0) + 1; counts[m.away] = counts.get(m.away, 0) + 1
    return EloModel(r, home_adv, nu, counts, as_of, len(used))
