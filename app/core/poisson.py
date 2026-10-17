"""Score-grid maths: independent Poisson with optional Dixon-Coles low-score correction."""
from __future__ import annotations
import numpy as np
from scipy.stats import poisson

MAX_GOALS = 10


def score_matrix(lam_home: float, lam_away: float, rho: float = 0.0, max_goals: int = MAX_GOALS) -> np.ndarray:
    """Joint probability grid P[h, a], rows = home goals. Always sums to 1."""
    if lam_home <= 0 or lam_away <= 0:
        raise ValueError("Expected goals must be positive.")
    g = np.arange(max_goals + 1)
    m = np.outer(poisson.pmf(g, lam_home), poisson.pmf(g, lam_away))
    if rho != 0.0:
        tau = {(0, 0): 1 - lam_home * lam_away * rho, (0, 1): 1 + lam_home * rho,
               (1, 0): 1 + lam_away * rho, (1, 1): 1 - rho}
        if min(tau.values()) <= 0:
            raise ValueError("rho is outside the valid range for these expected goals.")
        for (h, a), t in tau.items():
            m[h, a] *= t
    return m / m.sum()  # renormalise: removes truncation mass and DC drift


def markets(m: np.ndarray, top_scores: int = 6) -> dict:
    n = m.shape[0]
    h, a = np.indices(m.shape)
    out = {
        "home_win": float(m[h > a].sum()),
        "draw": float(m[h == a].sum()),
        "away_win": float(m[h < a].sum()),
        "btts_yes": float(m[(h > 0) & (a > 0)].sum()),
    }
    out["btts_no"] = 1.0 - out["btts_yes"]
    total = h + a
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        over = float(m[total > line].sum())
        key = str(line).replace(".", "_")
        out[f"over_{key}"], out[f"under_{key}"] = over, 1.0 - over
    idx = np.dstack(np.unravel_index(np.argsort(-m, axis=None), m.shape))[0][:top_scores]
    out["top_scores"] = [{"score": f"{i}-{j}", "probability": float(m[i, j])} for i, j in idx]
    out["grid_size"] = n
    return out
