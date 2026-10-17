"""Deterministic SYNTHETIC data for tests only. Never imported by the application."""
from datetime import date, timedelta
import numpy as np
from app.core.strength import Match


def make_league(n_teams=12, seasons=2, seed=1, home_adv=0.25, rho=0.0):
    rng = np.random.default_rng(seed)
    teams = [f"T{i:02d}" for i in range(n_teams)]
    att = dict(zip(teams, rng.normal(0, 0.25, n_teams))); dfn = dict(zip(teams, rng.normal(0, 0.25, n_teams)))
    matches, d, i = [], date(2022, 8, 1), 0
    for _ in range(seasons):
        for h in teams:
            for a in teams:
                if h == a: continue
                lh = np.exp(0.25 + home_adv + att[h] - dfn[a]); la = np.exp(0.25 + att[a] - dfn[h])
                matches.append(Match(d + timedelta(days=int(i // 6)), h, a, int(rng.poisson(lh)), int(rng.poisson(la)), f"s{i}"))
                i += 1
        d = matches[-1].date + timedelta(days=60); i = 0
    matches.sort(key=lambda m: m.date)
    return matches, att, dfn
