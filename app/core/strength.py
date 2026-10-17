"""Time-weighted, ridge-regularised Poisson attack/defence model (Dixon-Coles style).

log(lam_home) = mu + home_adv + attack[home] - defence[away]
log(lam_away) = mu + attack[away] - defence[home]

Leakage guard: fit() only ever uses matches strictly before `as_of`.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import poisson
from app.core.poisson import score_matrix, markets


class InsufficientData(ValueError):
    pass


@dataclass
class Match:
    date: date
    home: str
    away: str
    hg: int | None = None
    ag: int | None = None
    id: str | None = None


@dataclass
class StrengthModel:
    mu: float
    home_adv: float
    attack: dict
    defence: dict
    rho: float
    counts: dict
    as_of: date
    n_matches: int
    params: dict = field(default_factory=dict)
    version: str = "dc-poisson-v1"

    def expected_goals(self, home: str, away: str, min_matches: int = 5):
        for t in (home, away):
            if self.counts.get(t, 0) < min_matches:
                raise InsufficientData(f"{t}: only {self.counts.get(t, 0)} prior matches (need {min_matches}).")
        lh = float(np.exp(self.mu + self.home_adv + self.attack[home] - self.defence[away]))
        la = float(np.exp(self.mu + self.attack[away] - self.defence[home]))
        return lh, la

        def grid(self, home: str, away: str, min_matches: int = 5):
        """Joint score grid (rows = home goals) for this fixture."""
        lh, la = self.expected_goals(home, away, min_matches)
        return score_matrix(lh, la, self.rho)



def fit(matches: list[Match], as_of: date, xi: float = 0.0019, ridge: float = 2.0,
        min_total: int = 30, window_days: int | None = None) -> StrengthModel:
    """xi: per-day decay (0.0019 ~ 365-day half-life). ridge: shrinkage toward average team."""
    used = [m for m in matches if m.date < as_of and m.hg is not None and m.ag is not None
            and (window_days is None or (as_of - m.date).days <= window_days)]
    if len(used) < min_total:
        raise InsufficientData(f"{len(used)} completed matches before {as_of}; need {min_total}.")
    teams = sorted({m.home for m in used} | {m.away for m in used})
    ix = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    hi = np.array([ix[m.home] for m in used]); ai = np.array([ix[m.away] for m in used])
    hg = np.array([m.hg for m in used], float); ag = np.array([m.ag for m in used], float)
    w = np.exp(-xi * np.array([(as_of - m.date).days for m in used], float))

    def unpack(x):
        return x[0], x[1], x[2:2 + n], x[2 + n:]

    def loss(x):
        mu, h, a, d = unpack(x)
        lh = np.exp(mu + h + a[hi] - d[ai]); la = np.exp(mu + a[ai] - d[hi])
        nll = -(w * (hg * np.log(lh) - lh)).sum() - (w * (ag * np.log(la) - la)).sum()
        nll += ridge * ((a ** 2).sum() + (d ** 2).sum())
        rh, ra = w * (hg - lh), w * (ag - la)
        g = np.zeros_like(x)
        g[0] = -(rh.sum() + ra.sum()); g[1] = -rh.sum()
        ga = -np.bincount(hi, rh, n) - np.bincount(ai, ra, n) + 2 * ridge * a
        gd = np.bincount(ai, rh, n) + np.bincount(hi, ra, n) + 2 * ridge * d
        g[2:2 + n], g[2 + n:] = ga, gd
        return nll, g

    x0 = np.zeros(2 + 2 * n); x0[0] = np.log(max((hg.mean() + ag.mean()) / 2, 0.1))
    res = minimize(loss, x0, jac=True, method="L-BFGS-B")
    if not res.success:
        raise RuntimeError(f"Strength fit did not converge: {res.message}")
    mu, h, a, d = unpack(res.x)
    lh = np.exp(mu + h + a[hi] - d[ai]); la = np.exp(mu + a[ai] - d[hi])

    def neg_ll(rho):  # weighted Dixon-Coles low-score likelihood term
        t = np.ones(len(used))
        t = np.where((hg == 0) & (ag == 0), 1 - lh * la * rho, t)
        t = np.where((hg == 0) & (ag == 1), 1 + lh * rho, t)
        t = np.where((hg == 1) & (ag == 0), 1 + la * rho, t)
        t = np.where((hg == 1) & (ag == 1), 1 - rho, t)
        if (t <= 0).any():
            return 1e12
        return -(w * np.log(t)).sum()

    rho = float(minimize_scalar(neg_ll, bounds=(-0.3, 0.3), method="bounded").x)
    counts = {t: 0 for t in teams}
    for m in used:
        counts[m.home] += 1; counts[m.away] += 1
    return StrengthModel(float(mu), float(h), dict(zip(teams, a.astype(float))), dict(zip(teams, d.astype(float))),
                         rho, counts, as_of, len(used), {"xi": xi, "ridge": ridge, "window_days": window_days})
